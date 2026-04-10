"""APEX BOT -   (Partial Exit) 
      →    

 :
  Step 1:  50%  → 25%  ( )
  Step 2:  100%  → 50%  ()
  Step 3:  150%  → 25%  ( )
  :"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from loguru import logger


@dataclass
class PartialExitLevel:
    """docstring"""
    profit_pct: float     # 익절 발동 수익률 (예: 0.05 = 5%)
    exit_ratio: float     # 청산 비율 (예: 0.25 = 25%)
    executed: bool = False


@dataclass
class PartialExitState:
    """docstring"""
    market: str
    entry_price: float
    initial_volume: float
    remaining_volume: float
    take_profit: float     # 원래 목표가
    levels: List[PartialExitLevel] = field(default_factory=list)
    total_exited_ratio: float = 0.0  # 누적 청산 비율

    def __post_init__(self):
        if not self.levels:
            # 기본 3단계 청산 레벨
            tp_range = self.take_profit - self.entry_price
            if tp_range <= 0:
                tp_range = self.entry_price * 0.05
            self.levels = [
                PartialExitLevel(
                    profit_pct=(self.entry_price * 1 + tp_range * 0.5 - self.entry_price)
                               / self.entry_price,
                    exit_ratio=0.25,
                ),
                PartialExitLevel(
                    profit_pct=(self.entry_price + tp_range * 1.0 - self.entry_price)
                               / self.entry_price,
                    exit_ratio=0.50,
                ),
                PartialExitLevel(
                    profit_pct=(self.entry_price + tp_range * 1.5 - self.entry_price)
                               / self.entry_price,
                    exit_ratio=0.25,
                ),
            ]


class PartialExitManager:
    """:
        #   
        partial_exit.add_position(market, entry_price, volume, take_profit)

        #    ( )
        exit_vol = partial_exit.check(market, current_price)
        if exit_vol > 0:
            await executor.execute(sell partial exit_vol)

        #   
        partial_exit.remove_position(market)"""

    def __init__(self):
        self._positions: Dict[str, PartialExitState] = {}

    # ── Public API ──────────────────────────────────────────────────

    def add_position(
        self,
        market: str,
        entry_price: float,
        volume: float,
        take_profit: float,
        custom_levels: Optional[List[Tuple[float, float]]] = None,
    ):
        """Args:
            market:         (: KRW-BTC)
            entry_price:  
            volume:        
            take_profit:   (100%  )
            custom_levels: [(, ), ...]"""
        if entry_price <= 0 or volume <= 0:
            return

        state = PartialExitState(
            market=market,
            entry_price=entry_price,
            initial_volume=volume,
            remaining_volume=volume,
            take_profit=take_profit,
        )

        if custom_levels:
            state.levels = [
                PartialExitLevel(profit_pct=pct, exit_ratio=ratio)
                for pct, ratio in custom_levels
            ]

        self._positions[market] = state
        logger.debug(
            f"   | {market} | ={entry_price:,.0f} | "
            f"={take_profit:,.0f} | {len(state.levels)}단계"
        )

    def check(self, market: str, current_price: float) -> float:
        """→   

        Returns:
               (0  )"""
        state = self._positions.get(market)
        if not state or state.remaining_volume <= 0:
            return 0.0

        current_return = (current_price - state.entry_price) / state.entry_price

        for level in state.levels:
            if level.executed:
                continue
            if current_return >= level.profit_pct:
                # 청산 수량 계산 (초기 수량 기준)
                exit_volume = state.initial_volume * level.exit_ratio
                exit_volume = min(exit_volume, state.remaining_volume)
                # 코인별 소수점 내림 (찌꺼기 방지)
                try:
                    from core.engine import _floor_vol as _fv
                    exit_volume = _fv(market, exit_volume)
                except Exception:
                    exit_volume = round(exit_volume, 4)

                if exit_volume <= 0:
                    continue

                level.executed = True
                state.remaining_volume -= exit_volume
                state.total_exited_ratio += level.exit_ratio

                logger.info(
                    f"    | {market} | "
                    f"={current_return:.2%} ≥ ={level.profit_pct:.2%} | "
                    f"={exit_volume:.6f} ({level.exit_ratio:.0%}) | "
                    f"잔량={state.remaining_volume:.6f}"
                )
                return exit_volume

        return 0.0

    def get_remaining_volume(self, market: str) -> float:
        """docstring"""
        state = self._positions.get(market)
        return state.remaining_volume if state else 0.0

    def get_exited_ratio(self, market: str) -> float:
        """docstring"""
        state = self._positions.get(market)
        return state.total_exited_ratio if state else 0.0

    def remove_position(self, market: str):
        """docstring"""
        self._positions.pop(market, None)

    def restore_executed_levels(self, market: str, exited_ratio: float):
        """DB  :     executed=True  
        exited_ratio: DB    (: 0.25 → 1 )"""
        state = self._positions.get(market)
        if not state:
            return
        cumulative = 0.0
        for level in state.levels:
            cumulative += level.exit_ratio
            if exited_ratio >= cumulative - 0.001:
                level.executed = True
                state.total_exited_ratio = cumulative
        state.remaining_volume = state.initial_volume * (1.0 - state.total_exited_ratio)

    def get_state(self, market: str) -> Optional[PartialExitState]:
        return self._positions.get(market)

    def get_all_states(self) -> Dict[str, PartialExitState]:
        return self._positions.copy()

    def pending_levels(self, market: str) -> int:
        """docstring"""
        state = self._positions.get(market)
        if not state:
            return 0
        return sum(1 for lv in state.levels if not lv.executed)
