# risk/stop_loss/trailing_stop.py ???몃젅?쇰쭅 ?ㅽ깙 愿由ъ옄
"""
?섏씡 +2% ?ъ꽦 ??怨좎젏 ?鍮?-1.5% ?섎씫??留ㅻ룄 ?몃━嫄?
- ?ъ??섎퀎 怨좎젏(peak_price) 異붿쟻
- _check()瑜?硫붿씤 猷⑦봽 ?ъ씠?대쭏???몄텧
"""

from dataclasses import dataclass, field
from typing import Dict, Optional
from utils.logger import logger


@dataclass
class TrailingState:
    market:       str
    entry_price:  float
    peak_price:   float
    activated:    bool  = False  # +ACTIVATE_PCT ?ъ꽦 ?щ?
    trail_price:  float = 0.0   # ?꾩옱 ?몃젅???먯젅媛


class TrailingStopManager:
    """
    ?ъ??섎퀎 ?몃젅?쇰쭅 ?ㅽ깙 ?곹깭 愿由?
    """
    ACTIVATE_PCT  = 0.02   # +2% ?ъ꽦???몃젅?쇰쭅 ?쒖꽦??
    TRAIL_PCT     = 0.015  # 怨좎젏 ?鍮?-1.5% ?섎씫??留ㅻ룄

    def __init__(self):
        self._states: Dict[str, TrailingState] = {}

    def register(self, market: str, entry_price: float):
        """?좉퇋 ?ъ????깅줉"""
        self._states[market] = TrailingState(
            market=market,
            entry_price=entry_price,
            peak_price=entry_price,
        )
        logger.debug(f"[Trail] ?깅줉: {market} @ {entry_price}")

    def unregister(self, market: str):
        """?ъ???醫낅즺???댁젣"""
        self._states.pop(market, None)

    def update(self, market: str, current_price: float) -> Optional[str]:
        """
        媛寃??낅뜲?댄듃 諛??몃━嫄??뺤씤
        Returns: dict {"action": "SELL"|None, "profit_pct": float}
        """
        state = self._states.get(market)
        if state is None:
            return None

        # 怨좎젏 媛깆떊
        if current_price > state.peak_price:
            state.peak_price = current_price

        profit_pct = (current_price - state.entry_price) / state.entry_price

        # ?쒖꽦??泥댄겕
        if not state.activated and profit_pct >= self.ACTIVATE_PCT:
            state.activated   = True
            state.trail_price = state.peak_price * (1 - self.TRAIL_PCT)
            logger.info(
                f"[Trail] ???쒖꽦?? {market} "
                f"?섏씡={profit_pct*100:.2f}% "
                f"trail_price={state.trail_price:.2f}"
            )

        if not state.activated:
            return None

        # ?몃젅??媛寃?媛깆떊 (怨좎젏 ?ㅻ? ?뚮쭏??
        new_trail = state.peak_price * (1 - self.TRAIL_PCT)
        if new_trail > state.trail_price:
            state.trail_price = new_trail

        # ?몃━嫄??뺤씤
        if current_price <= state.trail_price:
            drop_pct = (state.peak_price - current_price) / state.peak_price
            logger.info(
                f"[Trail] ?뵶 諛쒕룞: {market} "
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
        engine.py ?명솚 蹂꾩묶 ??ATR 湲곕컲 珥덇린 ?몃젅???ㅼ젙 ?ы븿
        - atr > 0 ?대㈃ ATR x 2.0 ??珥덇린 ?몃젅????쑝濡??ъ슜
        - stop_loss > 0 ?대㈃ 珥덇린 ?먯젅媛濡??ъ슜
        """
        self.register(market, entry_price)
        state = self._states.get(market)
        if state and atr > 0:
            # ATR 湲곕컲 珥덇린 ?몃젅??媛寃??ㅼ젙
            atr_trail = entry_price - (atr * 2.0)
            if stop_loss > 0:
                # stop_loss? ATR ?몃젅??以??믪? 寃??ъ슜 (??蹂댁닔??
                state.trail_price = max(stop_loss, atr_trail)
            else:
                state.trail_price = atr_trail
            logger.debug(
                f"[Trail] ATR 珥덇린 ?몃젅???ㅼ젙: {market} "
                f"trail={state.trail_price:.2f} "
                f"(ATR={atr:.2f} x 2.0)"
            )
        elif state and stop_loss > 0:
            state.trail_price = stop_loss

    def remove_position(self, market: str):
        """engine.py ?명솚 蹂꾩묶 ??unregister()? ?숈씪"""
        self.unregister(market)

    def get_status(self, market: str) -> Optional[dict]:
        state = self._states.get(market)
        if not state:
            return None
        profit_pct = (
            (state.peak_price - state.entry_price) / state.entry_price
            if state.entry_price > 0 else 0.0
        )
        return {
            "activated":    state.activated,
            "peak_price":   state.peak_price,
            "trail_price":  state.trail_price,
            "entry_price":  state.entry_price,
            "profit_pct":   profit_pct,
        }

