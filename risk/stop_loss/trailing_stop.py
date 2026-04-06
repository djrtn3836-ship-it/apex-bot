# risk/stop_loss/trailing_stop.py — 트레일링 스탑 관리자
"""
수익 +2% 달성 후 고점 대비 -1.5% 하락시 매도 트리거
- 포지션별 고점(peak_price) 추적
- _check()를 메인 루프 사이클마다 호출
"""

from dataclasses import dataclass, field
from typing import Dict, Optional
from utils.logger import logger


@dataclass
class TrailingState:
    market:       str
    entry_price:  float
    peak_price:   float
    activated:    bool  = False  # +ACTIVATE_PCT 달성 여부
    trail_price:  float = 0.0   # 현재 트레일 손절가


class TrailingStopManager:
    """
    포지션별 트레일링 스탑 상태 관리
    """
    ACTIVATE_PCT  = 0.02   # +2% 달성시 트레일링 활성화
    TRAIL_PCT     = 0.015  # 고점 대비 -1.5% 하락시 매도

    def __init__(self):
        self._states: Dict[str, TrailingState] = {}

    def register(self, market: str, entry_price: float):
        """신규 포지션 등록"""
        self._states[market] = TrailingState(
            market=market,
            entry_price=entry_price,
            peak_price=entry_price,
        )
        logger.debug(f"[Trail] 등록: {market} @ {entry_price}")

    def unregister(self, market: str):
        """포지션 종료시 해제"""
        self._states.pop(market, None)

    def update(self, market: str, current_price: float) -> Optional[str]:
        """
        가격 업데이트 및 트리거 확인
        Returns: "SELL" if trailing stop triggered, else None
        """
        state = self._states.get(market)
        if state is None:
            return None

        # 고점 갱신
        if current_price > state.peak_price:
            state.peak_price = current_price

        profit_pct = (current_price - state.entry_price) / state.entry_price

        # 활성화 체크
        if not state.activated and profit_pct >= self.ACTIVATE_PCT:
            state.activated   = True
            state.trail_price = state.peak_price * (1 - self.TRAIL_PCT)
            logger.info(
                f"[Trail] ✅ 활성화: {market} "
                f"수익={profit_pct*100:.2f}% "
                f"trail_price={state.trail_price:.2f}"
            )

        if not state.activated:
            return None

        # 트레일 가격 갱신 (고점 오를 때마다)
        new_trail = state.peak_price * (1 - self.TRAIL_PCT)
        if new_trail > state.trail_price:
            state.trail_price = new_trail

        # 트리거 확인
        if current_price <= state.trail_price:
            drop_pct = (state.peak_price - current_price) / state.peak_price
            logger.info(
                f"[Trail] 🔴 발동: {market} "
                f"peak={state.peak_price:.2f} "
                f"current={current_price:.2f} "
                f"drop={drop_pct*100:.2f}%"
            )
            return "SELL"

        return None


    def add_position(
        self,
        market: str,
        entry_price: float,
        stop_loss: float = 0.0,
        atr: float = 0.0,
        initial_stop: float = 0.0,
        take_profit: float = 0.0,
        **kwargs,
    ):
        """
        engine.py 호환 별칭 — ATR 기반 초기 트레일 설정 포함
        - atr > 0 이면 ATR x 2.0 을 초기 트레일 폭으로 사용
        - stop_loss > 0 이면 초기 손절가로 사용
        """
        self.register(market, entry_price)
        state = self._states.get(market)
        if state and atr > 0:
            # ATR 기반 초기 트레일 가격 설정
            atr_trail = entry_price - (atr * 2.0)
            if stop_loss > 0:
                # stop_loss와 ATR 트레일 중 높은 것 사용 (더 보수적)
                state.trail_price = max(stop_loss, atr_trail)
            else:
                state.trail_price = atr_trail
            logger.debug(
                f"[Trail] ATR 초기 트레일 설정: {market} "
                f"trail={state.trail_price:.2f} "
                f"(ATR={atr:.2f} x 2.0)"
            )
        elif state and stop_loss > 0:
            state.trail_price = stop_loss

    def remove_position(self, market: str):
        """engine.py 호환 별칭 — unregister()와 동일"""
        self.unregister(market)

    def get_status(self, market: str) -> Optional[dict]:
        state = self._states.get(market)
        if not state:
            return None
        profit_pct = 0.0
        return {
            "activated":    state.activated,
            "peak_price":   state.peak_price,
            "trail_price":  state.trail_price,
            "entry_price":  state.entry_price,
        }
