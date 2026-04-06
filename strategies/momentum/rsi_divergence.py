"""
APEX BOT - RSI 다이버전스 전략
RSI 과매수/과매도 + 가격-RSI 다이버전스 감지
"""
import pandas as pd
import numpy as np
from typing import Optional
from strategies.base_strategy import BaseStrategy, StrategySignal, SignalType


class RSIDivergenceStrategy(BaseStrategy):
    NAME = "rsi_divergence"
    DESCRIPTION = "RSI 과매수/과매도 + 불-베어 다이버전스"
    WEIGHT = 1.0
    MIN_CANDLES = 50

    def _default_params(self) -> dict:
        return {
            "rsi_period": 14,
            "oversold": 35,
            "overbought": 65,
            "extreme_oversold": 25,
            "extreme_overbought": 75,
            "divergence_window": 14,
        }

    def _find_divergence(self, close: pd.Series, rsi: pd.Series, window: int = 14) -> str:
        """
        가격-RSI 다이버전스 감지
        - 강세 다이버전스: 가격은 신저점 but RSI는 고점
        - 약세 다이버전스: 가격은 신고점 but RSI는 저점
        """
        recent_close = close.iloc[-window:]
        recent_rsi = rsi.iloc[-window:]

        price_low_idx = recent_close.idxmin()
        price_high_idx = recent_close.idxmax()

        # 강세 다이버전스 (불리시 다이버전스)
        if price_low_idx > recent_close.index[0]:
            price_making_lower_low = recent_close.iloc[-1] < recent_close.iloc[0]
            rsi_making_higher_low = recent_rsi.iloc[-1] > recent_rsi.iloc[0]
            if price_making_lower_low and rsi_making_higher_low:
                return "bullish"

        # 약세 다이버전스 (베어리시 다이버전스)
        if price_high_idx > recent_close.index[0]:
            price_making_higher_high = recent_close.iloc[-1] > recent_close.iloc[0]
            rsi_making_lower_high = recent_rsi.iloc[-1] < recent_rsi.iloc[0]
            if price_making_higher_high and rsi_making_lower_high:
                return "bearish"

        return "none"

    def generate_signal(self, df: pd.DataFrame, market: str,
                        timeframe: str = "60") -> Optional[StrategySignal]:
        if not self.validate_df(df) or "rsi_14" not in df.columns:
            return None

        close = df["close"]
        rsi = df["rsi_14"]
        atr = df.get("atr_14", (df["high"] - df["low"]).rolling(14).mean())

        curr_rsi = float(rsi.iloc[-1])
        curr_close = float(close.iloc[-1])
        curr_atr = float(atr.iloc[-1])

        p = self.params
        divergence = self._find_divergence(close, rsi, p["divergence_window"])

        # ─ 매수 조건 ─
        if curr_rsi <= p["oversold"]:
            if curr_rsi <= p["extreme_oversold"]:
                confidence = 0.80
                score = 0.9
                reason = f"RSI 극단 과매도 ({curr_rsi:.1f})"
            elif divergence == "bullish":
                confidence = 0.75
                score = 0.8
                reason = f"RSI 과매도 + 강세 다이버전스 ({curr_rsi:.1f})"
            else:
                confidence = 0.60
                score = 0.6
                reason = f"RSI 과매도 ({curr_rsi:.1f})"

            return self._create_signal(
                SignalType.BUY, score, confidence, market,
                curr_close, curr_close - curr_atr * 1.5, curr_close + curr_atr * 3.0,
                reason, timeframe, {"rsi": curr_rsi, "divergence": divergence}
            )

        # ─ 매도 조건 ─
        if curr_rsi >= p["overbought"]:
            if curr_rsi >= p["extreme_overbought"]:
                confidence = 0.80
                score = -0.9
                reason = f"RSI 극단 과매수 ({curr_rsi:.1f})"
            elif divergence == "bearish":
                confidence = 0.75
                score = -0.8
                reason = f"RSI 과매수 + 약세 다이버전스 ({curr_rsi:.1f})"
            else:
                confidence = 0.60
                score = -0.6
                reason = f"RSI 과매수 ({curr_rsi:.1f})"

            return self._create_signal(
                SignalType.SELL, score, confidence, market,
                curr_close, curr_close + curr_atr * 1.5, curr_close - curr_atr * 3.0,
                reason, timeframe, {"rsi": curr_rsi, "divergence": divergence}
            )

        return None
