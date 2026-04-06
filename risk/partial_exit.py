"""
APEX BOT - 부분 청산 (Partial Exit) 관리자
목표가 도달 시 포지션 일부 익절 → 나머지는 트레일링으로 수익 극대화

청산 단계:
  Step 1: 목표가 50% 도달 → 25% 청산 (수익 확정)
  Step 2: 목표가 100% 도달 → 50% 청산 (익절)
  Step 3: 목표가 150% 도달 → 25% 청산 (추가 익절)
  나머지: 트레일링 스탑으로 계속 추적
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from loguru import logger


@dataclass
class PartialExitLevel:
    """개별 청산 단계"""
    profit_pct: float     # 익절 발동 수익률 (예: 0.05 = 5%)
    exit_ratio: float     # 청산 비율 (예: 0.25 = 25%)
    executed: bool = False


@dataclass
class PartialExitState:
    """포지션별 부분 청산 상태"""
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
    """
    부분 청산 관리자

    엔진 연동 방법:
        # 포지션 진입 시
        partial_exit.add_position(market, entry_price, volume, take_profit)

        # 가격 업데이트 시 (매 분)
        exit_vol = partial_exit.check(market, current_price)
        if exit_vol > 0:
            await executor.execute(sell partial exit_vol)

        # 포지션 종료 시
        partial_exit.remove_position(market)
    """

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
        """
        포지션 추가

        Args:
            market:       마켓 코드 (예: KRW-BTC)
            entry_price:  진입가
            volume:       매수 수량
            take_profit:  목표가 (100% 달성 기준)
            custom_levels: [(수익률, 청산비율), ...] 커스텀 단계
        """
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
            f"📊 부분청산 설정 | {market} | 진입={entry_price:,.0f} | "
            f"목표={take_profit:,.0f} | {len(state.levels)}단계"
        )

    def check(self, market: str, current_price: float) -> float:
        """
        가격 업데이트 → 청산 수량 반환

        Returns:
            청산할 코인 수량 (0이면 청산 없음)
        """
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

                if exit_volume <= 0:
                    continue

                level.executed = True
                state.remaining_volume -= exit_volume
                state.total_exited_ratio += level.exit_ratio

                logger.info(
                    f"💰 부분 청산 발동 | {market} | "
                    f"수익률={current_return:.2%} ≥ 목표={level.profit_pct:.2%} | "
                    f"청산={exit_volume:.6f} ({level.exit_ratio:.0%}) | "
                    f"잔량={state.remaining_volume:.6f}"
                )
                return exit_volume

        return 0.0

    def get_remaining_volume(self, market: str) -> float:
        """남은 포지션 수량"""
        state = self._positions.get(market)
        return state.remaining_volume if state else 0.0

    def get_exited_ratio(self, market: str) -> float:
        """누적 청산 비율"""
        state = self._positions.get(market)
        return state.total_exited_ratio if state else 0.0

    def remove_position(self, market: str):
        """포지션 제거"""
        self._positions.pop(market, None)

    def get_state(self, market: str) -> Optional[PartialExitState]:
        return self._positions.get(market)

    def get_all_states(self) -> Dict[str, PartialExitState]:
        return self._positions.copy()

    def pending_levels(self, market: str) -> int:
        """미실행 청산 단계 수"""
        state = self._positions.get(market)
        if not state:
            return 0
        return sum(1 for lv in state.levels if not lv.executed)
