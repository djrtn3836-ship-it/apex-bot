"""APEX BOT -   v1.0
    /  

:
  Bullish OB:       (  )
  Bearish OB:       (  )

 :
  1.  :  N ATR 2  
  2. OB :       ( ATR 10% )
  3. :      OB      
  4. :        2%  OB"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List
import numpy as np
import pandas as pd
from loguru import logger


@dataclass
class OrderBlock:
    ob_type: str        # "bullish" | "bearish"
    high: float
    low: float
    mid: float
    candle_idx: int
    strength: float     # 0~1
    impulse_atr: float  # 충격 이동 크기 (ATR 배수)


@dataclass
class OrderBlockSignal:
    nearest_bullish: Optional[OrderBlock] = None
    nearest_bearish: Optional[OrderBlock] = None
    price_in_bullish: bool = False
    price_in_bearish: bool = False
    dist_bullish_pct: float = 999.0
    dist_bearish_pct: float = 999.0
    signal: str = "NEUTRAL"     # BUY_ZONE | SELL_ZONE | NEUTRAL
    confidence: float = 0.0
    ob_count: int = 0


class OrderBlockDetector:
    """Parameters
    ----------
    impulse_mult : float    ATR  ( 2.0)
    lookback     : int       ( 100)
    max_obs      : int      OB  ( 5)"""

    def __init__(
        self,
        impulse_mult: float = 2.0,
        lookback: int = 100,
        max_obs: int = 5,
    ):
        self.impulse_mult = impulse_mult
        self.lookback     = lookback
        self.max_obs      = max_obs

    def detect(self, df: pd.DataFrame, current_price: float) -> OrderBlockSignal:
        try:
            if df is None or len(df) < 30:
                return OrderBlockSignal()

            df = df.tail(self.lookback).copy().reset_index(drop=True)
            df.columns = [c.lower() for c in df.columns]
            required = {"open", "high", "low", "close"}
            if not required.issubset(set(df.columns)):
                return OrderBlockSignal()

            atr = self._calc_atr(df)
            if atr <= 0:
                return OrderBlockSignal()

            bullish_obs, bearish_obs = [], []
            scan_end = len(df) - 3

            for i in range(5, scan_end):
                ob = self._check_bullish(df, i, atr)
                if ob:
                    bullish_obs.append(ob)
                ob2 = self._check_bearish(df, i, atr)
                if ob2:
                    bearish_obs.append(ob2)

            # 신선도 필터
            bullish_obs = [o for o in bullish_obs if self._is_fresh(o, df, current_price, "bullish")]
            bearish_obs = [o for o in bearish_obs if self._is_fresh(o, df, current_price, "bearish")]

            # 강도 순 정렬, 상위 N개
            bullish_obs = sorted(bullish_obs, key=lambda x: x.strength, reverse=True)[:self.max_obs]
            bearish_obs = sorted(bearish_obs, key=lambda x: x.strength, reverse=True)[:self.max_obs]

            return self._build_signal(bullish_obs, bearish_obs, current_price)

        except Exception as e:
            logger.debug(f"  : {e}")
            return OrderBlockSignal()

    def _check_bullish(self, df: pd.DataFrame, i: int, atr: float) -> Optional[OrderBlock]:
        c = df.iloc[i]
        if c["close"] >= c["open"]:
            return None
        body = abs(c["open"] - c["close"])
        if body < atr * 0.1:
            return None
        impulse = self._find_impulse(df, i, "up", atr)
        if impulse is None:
            return None
        strength = min(1.0, impulse / (self.impulse_mult * atr))
        freshness = (i / len(df)) * 0.3 + 0.7
        return OrderBlock(
            ob_type="bullish",
            high=c["high"], low=c["low"],
            mid=(c["high"] + c["low"]) / 2,
            candle_idx=i,
            strength=round(strength * freshness, 4),
            impulse_atr=round(impulse / atr, 2),
        )

    def _check_bearish(self, df: pd.DataFrame, i: int, atr: float) -> Optional[OrderBlock]:
        c = df.iloc[i]
        if c["close"] <= c["open"]:
            return None
        body = abs(c["close"] - c["open"])
        if body < atr * 0.1:
            return None
        impulse = self._find_impulse(df, i, "down", atr)
        if impulse is None:
            return None
        strength = min(1.0, impulse / (self.impulse_mult * atr))
        freshness = (i / len(df)) * 0.3 + 0.7
        return OrderBlock(
            ob_type="bearish",
            high=c["high"], low=c["low"],
            mid=(c["high"] + c["low"]) / 2,
            candle_idx=i,
            strength=round(strength * freshness, 4),
            impulse_atr=round(impulse / atr, 2),
        )

    def _find_impulse(self, df, start, direction, atr) -> Optional[float]:
        threshold = atr * self.impulse_mult
        look_ahead = min(5, len(df) - start - 1)
        ref = df.iloc[start]["close"]
        for j in range(1, look_ahead + 1):
            curr = df.iloc[start + j]["close"]
            move = (curr - ref) if direction == "up" else (ref - curr)
            if move >= threshold:
                return move
        return None

    def _is_fresh(self, ob, df, current_price, ob_type) -> bool:
        after = df.iloc[ob.candle_idx + 1:]
        if ob_type == "bullish":
            return not (after["low"] < ob.low).any()
        else:
            return not (after["high"] > ob.high).any()

    def _build_signal(self, bullish_obs, bearish_obs, price) -> OrderBlockSignal:
        sig = OrderBlockSignal(ob_count=len(bullish_obs) + len(bearish_obs))

        # 가장 가까운 강세 OB (현재가 아래)
        below = [o for o in bullish_obs if o.high <= price * 1.02]
        if below:
            nb = min(below, key=lambda o: price - o.mid)
            sig.nearest_bullish   = nb
            sig.dist_bullish_pct  = abs(price - nb.mid) / nb.mid * 100
            sig.price_in_bullish  = nb.low <= price <= nb.high

        # 가장 가까운 약세 OB (현재가 위)
        above = [o for o in bearish_obs if o.low >= price * 0.98]
        if above:
            na = min(above, key=lambda o: o.mid - price)
            sig.nearest_bearish   = na
            sig.dist_bearish_pct  = abs(price - na.mid) / na.mid * 100
            sig.price_in_bearish  = na.low <= price <= na.high

        # 종합 신호
        if sig.price_in_bullish and sig.nearest_bullish:
            sig.signal, sig.confidence = "BUY_ZONE", sig.nearest_bullish.strength
        elif sig.price_in_bearish and sig.nearest_bearish:
            sig.signal, sig.confidence = "SELL_ZONE", sig.nearest_bearish.strength
        elif sig.nearest_bullish and sig.dist_bullish_pct < 1.5:
            sig.signal, sig.confidence = "BUY_ZONE", sig.nearest_bullish.strength * 0.7
        elif sig.nearest_bearish and sig.dist_bearish_pct < 1.5:
            sig.signal, sig.confidence = "SELL_ZONE", sig.nearest_bearish.strength * 0.7
        else:
            sig.signal, sig.confidence = "NEUTRAL", 0.0

        return sig

    @staticmethod
    def _calc_atr(df: pd.DataFrame, period: int = 14) -> float:
        try:
            h = df["high"].values
            l = df["low"].values
            c = df["close"].values
            tr = np.maximum(h[1:] - l[1:],
                 np.maximum(np.abs(h[1:] - c[:-1]),
                            np.abs(l[1:] - c[:-1])))
            if len(tr) < period:
                return float(np.mean(tr)) if len(tr) > 0 else 0.0
            return float(np.mean(tr[-period:]))
        except Exception:
            return 0.0
