"""
APEX BOT - Order Block + Market Structure Strategy
Smart Money Concept (SMC): Institutional order blocks, liquidity sweeps, FVG
"""
import pandas as pd
import numpy as np
from typing import Optional, List
from loguru import logger

from strategies.base_strategy import BaseStrategy, StrategySignal, SignalType


class OrderBlockStrategy(BaseStrategy):
    """
    Smart Money Concept (SMC) Order Block Strategy

    Core concepts:
    1. Order Block (OB)       – last candle before strong directional move
    2. Fair Value Gap (FVG)   – imbalance gap from rapid price movement
    3. Liquidity Sweep        – high/low sweep followed by reversal
    4. Break of Structure (BOS) – market structure shift
    """

    NAME = "OrderBlock_SMC"
    DESCRIPTION = "스마트머니 오더블록 + 시장구조 분석"
    WEIGHT = 1.5
    MIN_CANDLES = 40
    SUPPORTED_TIMEFRAMES = ["60", "240", "1440"]

    def _default_params(self) -> dict:
        return {
            "ob_lookback": 20,
            "fvg_min_size": 0.003,
            "swing_lookback": 5,
        }

    def generate_signal(
        self, df: pd.DataFrame, market: str, timeframe: str = "60"
    ) -> Optional[StrategySignal]:
        p = self.params
        if df is None or len(df) < p["ob_lookback"] + 10:
            return None

        try:
            close = float(df["close"].iloc[-1])
            atr   = float(df["atr"].iloc[-1]) if "atr" in df.columns else close * 0.015

            # 1. Market structure
            structure = self._analyze_market_structure(df)

            # 2. Order blocks
            bull_obs = self._find_bullish_order_blocks(df)
            bear_obs = self._find_bearish_order_blocks(df)

            # 3. FVG
            fvgs = self._find_fvg(df)

            # 4. Liquidity sweep
            liq_sweep = self._detect_liquidity_sweep(df)

            # ── BUY conditions ────────────────────────────────────────
            buy_signals = []

            for ob in bull_obs[-3:]:
                if ob["low"] <= close <= ob["high"] * 1.005:
                    buy_signals.append(f"강세OB:{ob['low']:,.0f}~{ob['high']:,.0f}")

            for fvg in fvgs:
                if fvg["type"] == "bullish" and fvg["low"] <= close <= fvg["high"]:
                    buy_signals.append(f"강세FVG:{fvg['low']:,.0f}~{fvg['high']:,.0f}")

            if liq_sweep["type"] == "bullish" and liq_sweep["recent"]:
                buy_signals.append("유동성 스윕 반전(강세)")

            if buy_signals and structure["bias"] in ("BULLISH", "NEUTRAL"):
                n          = len(buy_signals)
                confidence = min(0.85, 0.55 + n * 0.10)
                score      = min(1.0, n * 0.35)
                return self._create_signal(
                    SignalType.BUY, score, confidence, market,
                    entry_price=close,
                    stop_loss=close - atr * 2.0,
                    take_profit=close + atr * 4.0,
                    reason=" | ".join(buy_signals),
                    timeframe=timeframe,
                    metadata={
                        "structure": structure["bias"],
                        "bull_obs": len(bull_obs),
                        "fvgs": len(fvgs),
                        "sweep": liq_sweep["type"],
                    },
                )

            # ── SELL conditions ───────────────────────────────────────
            sell_signals = []

            for ob in bear_obs[-3:]:
                if ob["low"] * 0.995 <= close <= ob["high"]:
                    sell_signals.append(f"약세OB:{ob['low']:,.0f}~{ob['high']:,.0f}")

            if liq_sweep["type"] == "bearish" and liq_sweep["recent"]:
                sell_signals.append("유동성 스윕 반전(약세)")

            if sell_signals and structure["bias"] in ("BEARISH", "NEUTRAL"):
                n          = len(sell_signals)
                confidence = min(0.85, 0.55 + n * 0.10)
                score      = min(1.0, n * 0.35)
                return self._create_signal(
                    SignalType.SELL, score, confidence, market,
                    entry_price=close,
                    stop_loss=close + atr * 2.0,
                    take_profit=close - atr * 4.0,
                    reason=" | ".join(sell_signals),
                    timeframe=timeframe,
                    metadata={
                        "structure": structure["bias"],
                        "bear_obs": len(bear_obs),
                    },
                )

            return None

        except Exception as e:
            logger.error(f"{self.NAME} 오류 ({market}): {e}")
            return None

    # ── Internal helpers ─────────────────────────────────────────────

    def _analyze_market_structure(self, df: pd.DataFrame) -> dict:
        """BOS / CHoCH market structure analysis"""
        lb       = self.params["swing_lookback"]
        highs    = df["high"].rolling(lb).max()
        lows     = df["low"].rolling(lb).min()
        r_highs  = highs.tail(10)
        r_lows   = lows.tail(10)

        hh = r_highs.iloc[-1] > r_highs.iloc[0]
        hl = r_lows.iloc[-1]  > r_lows.iloc[0]
        lh = r_highs.iloc[-1] < r_highs.iloc[0]
        ll = r_lows.iloc[-1]  < r_lows.iloc[0]

        if hh and hl:
            bias = "BULLISH"
        elif lh and ll:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        return {"bias": bias, "hh": hh, "hl": hl, "lh": lh, "ll": ll}

    def _find_bullish_order_blocks(self, df: pd.DataFrame) -> List[dict]:
        """Detect bullish order blocks (bearish candle before a strong up-move)"""
        obs = []
        lb  = min(self.params["ob_lookback"], len(df) - 2)
        for i in range(2, lb):
            idx  = -(i + 1)
            c    = df.iloc[idx]
            nxt  = df.iloc[idx + 1]
            if (c["close"] < c["open"]                                       # bearish candle
                    and nxt["close"] > nxt["open"]                           # next is bullish
                    and (nxt["close"] - nxt["open"]) >
                        (c["open"] - c["close"]) * 1.5):                     # 1.5× rebound
                obs.append({"high": float(c["open"]),
                             "low":  float(c["close"]),
                             "idx":  idx})
        return obs

    def _find_bearish_order_blocks(self, df: pd.DataFrame) -> List[dict]:
        """Detect bearish order blocks (bullish candle before a strong down-move)"""
        obs = []
        lb  = min(self.params["ob_lookback"], len(df) - 2)
        for i in range(2, lb):
            idx  = -(i + 1)
            c    = df.iloc[idx]
            nxt  = df.iloc[idx + 1]
            if (c["close"] > c["open"]
                    and nxt["close"] < nxt["open"]
                    and (nxt["open"] - nxt["close"]) >
                        (c["close"] - c["open"]) * 1.5):
                obs.append({"high": float(c["close"]),
                             "low":  float(c["open"]),
                             "idx":  idx})
        return obs

    def _find_fvg(self, df: pd.DataFrame) -> List[dict]:
        """Fair Value Gap (imbalance) detection"""
        fvgs     = []
        min_size = self.params["fvg_min_size"]
        for i in range(-10, -2):
            c1, c2, c3 = df.iloc[i - 1], df.iloc[i], df.iloc[i + 1]
            # Bullish FVG: c1 high < c3 low
            if c3["low"] > c1["high"]:
                size = (c3["low"] - c1["high"]) / float(c2["close"])
                if size >= min_size:
                    fvgs.append({"type": "bullish",
                                 "low":  float(c1["high"]),
                                 "high": float(c3["low"]),
                                 "size": size})
            # Bearish FVG: c1 low > c3 high
            if c3["high"] < c1["low"]:
                size = (c1["low"] - c3["high"]) / float(c2["close"])
                if size >= min_size:
                    fvgs.append({"type": "bearish",
                                 "low":  float(c3["high"]),
                                 "high": float(c1["low"]),
                                 "size": size})
        return fvgs

    def _detect_liquidity_sweep(self, df: pd.DataFrame) -> dict:
        """Detect liquidity sweep (prior high/low touch then reversal)"""
        if len(df) < 24:
            return {"type": "none", "recent": False, "level": 0}

        recent     = df.tail(20)
        prev       = df.iloc[-22:-2]
        prev_high  = float(prev["high"].max())
        prev_low   = float(prev["low"].min())
        curr_high  = float(recent["high"].max())
        curr_low   = float(recent["low"].min())
        close      = float(df.iloc[-1]["close"])

        # Bearish sweep: broke above prev-high then closed below
        if curr_high > prev_high and close < prev_high:
            return {"type": "bearish", "recent": True, "level": prev_high}

        # Bullish sweep: broke below prev-low then closed above
        if curr_low < prev_low and close > prev_low:
            return {"type": "bullish", "recent": True, "level": prev_low}

        return {"type": "none", "recent": False, "level": 0}
