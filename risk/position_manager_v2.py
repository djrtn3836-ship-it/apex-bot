"""Apex Bot -    v2 (M4)
 /  /  /"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum
from loguru import logger


class ExitReason(Enum):
    STOP_LOSS       = "STOP_LOSS"
    TAKE_PROFIT     = "TAKE_PROFIT"
    BREAKEVEN_STOP  = "BREAKEVEN_STOP"
    PARTIAL_EXIT    = "PARTIAL_EXIT"
    TIME_EXIT       = "TIME_EXIT"
    SIGNAL_SELL     = "SIGNAL_SELL"
    EMERGENCY       = "EMERGENCY"


@dataclass
class PositionV2:
    market:          str
    entry_price:     float
    volume:          float
    amount_krw:      float
    stop_loss:       float
    take_profit:     float
    strategy:        str
    entry_time:      datetime = field(default_factory=datetime.now)
    pyramid_count:   int   = 0
    partial_exited:  bool  = False
    breakeven_set:   bool  = False
    max_price:       float = 0.0

    def __post_init__(self):
        self.max_price = self.entry_price


@dataclass
class ExitSignal:
    should_exit: bool
    reason:      ExitReason
    volume_pct:  float = 1.0   # 청산 비율 (1.0 = 전량)
    message:     str   = ""


class PositionManagerV2:
    """v2"""

    def __init__(
        self,
        max_hold_hours:      int   = 72,
        breakeven_trigger:   float = 0.02,
        partial_exit_1:      float = 0.03,
        partial_exit_1_pct:  float = 0.30,
        partial_exit_2:      float = 0.05,
        partial_exit_2_pct:  float = 0.30,
        pyramid_max:         int   = 2,
        pyramid_trigger:     float = 0.02,
    ):
        self.max_hold_hours     = max_hold_hours
        self.breakeven_trigger  = breakeven_trigger
        self.partial_exit_1     = partial_exit_1
        self.partial_exit_1_pct = partial_exit_1_pct
        self.partial_exit_2     = partial_exit_2
        self.partial_exit_2_pct = partial_exit_2_pct
        self.pyramid_max        = pyramid_max
        self.pyramid_trigger    = pyramid_trigger
        self.positions: Dict[str, PositionV2] = {}
        logger.info(" PositionManagerV2 ")

    def add_position(self, pos: PositionV2):
        self.positions[pos.market] = pos
        logger.info(f"   | {pos.market} | ={pos.entry_price:,.0f}")

    def remove_position(self, market: str):
        self.positions.pop(market, None)

    def check_exit(self, market: str, current_price: float) -> ExitSignal:
        """docstring"""
        pos = self.positions.get(market)
        if pos is None:
            return ExitSignal(False, ExitReason.SIGNAL_SELL)

        pnl = (current_price - pos.entry_price) / pos.entry_price
        pos.max_price = max(pos.max_price, current_price)

        # 1. 손절
        if current_price <= pos.stop_loss:
            return ExitSignal(True, ExitReason.STOP_LOSS, 1.0,
                f"손절: {pnl:.2%}")

        # 2. 손익분기 스탑 설정
        if pnl >= self.breakeven_trigger and not pos.breakeven_set:
            pos.stop_loss   = pos.entry_price * 1.001
            pos.breakeven_set = True
            logger.info(f"    | {market} | ={pos.stop_loss:,.0f}")

        # 3. 1차 부분익절
        if pnl >= self.partial_exit_1 and not pos.partial_exited:
            pos.partial_exited = True
            return ExitSignal(True, ExitReason.PARTIAL_EXIT,
                self.partial_exit_1_pct,
                f"1차 부분익절 {self.partial_exit_1_pct:.0%}: {pnl:.2%}")

        # 4. 2차 부분익절 (전량 청산)
        if pnl >= self.partial_exit_2 and pos.partial_exited:
            return ExitSignal(True, ExitReason.TAKE_PROFIT, 1.0,
                f"2차 익절: {pnl:.2%}")

        # 5. 시간 청산
        held_hours = (datetime.now() - pos.entry_time).total_seconds() / 3600
        if held_hours >= self.max_hold_hours:
            return ExitSignal(True, ExitReason.TIME_EXIT, 1.0,
                f"보유시간 초과: {held_hours:.1f}h")

        return ExitSignal(False, ExitReason.SIGNAL_SELL)

    def check_pyramid(self, market: str, current_price: float) -> Tuple[bool, float]:
        """→ (, )"""
        pos = self.positions.get(market)
        if pos is None:
            return False, 0.0
        if pos.pyramid_count >= self.pyramid_max:
            return False, 0.0

        pnl = (current_price - pos.entry_price) / pos.entry_price
        if pnl >= self.pyramid_trigger * (pos.pyramid_count + 1):
            add_ratio = 0.5 ** (pos.pyramid_count + 1)
            pos.pyramid_count += 1
            logger.info(
                f"  {pos.pyramid_count}/{self.pyramid_max} | "
                f"{market} | ={add_ratio:.0%}"
            )
            return True, add_ratio

        return False, 0.0

    def get_all_positions(self) -> Dict[str, PositionV2]:
        return self.positions.copy()
