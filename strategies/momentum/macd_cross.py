"""
APEX BOT - MACD 크로스 전략
MACD 골든/데드크로스 + 다이버전스 감지
"""
import pandas as pd
import numpy as np
from typing import Optional
from strategies.base_strategy import BaseStrategy, StrategySignal, SignalType


class MACDCrossStrategy(BaseStrategy):
    NAME = "macd_cross"
    DESCRIPTION = "MACD 골든/데드크로스 + 히스토그램 모멘텀"
    WEIGHT = 1.5
    MIN_CANDLES = 60

    def _default_params(self) -> dict:
        return {
            "fast": 12, "slow": 26, "signal": 9,
            "histogram_threshold": 0.0,
            "confirmation_candles": 2,     # 크로스 확인 캔들 수
            "min_histogram_change": 0.0,
        }

    def generate_signal(self, df: pd.DataFrame, market: str,
                        timeframe: str = "60") -> Optional[StrategySignal]:
        if not self.validate_df(df):
            return None

        close = df["close"]
        atr = df.get("atr_14", (df["high"] - df["low"]).rolling(14).mean())

        # MACD 값 로드 (미리 계산된 값 사용)
        if "macd" not in df.columns:
            return None

        macd_line = df["macd"]
        signal_line = df["macd_signal"]
        histogram = df["macd_hist"]

        # 현재/이전 값
        curr_macd = macd_line.iloc[-1]
        curr_signal = signal_line.iloc[-1]
        curr_hist = histogram.iloc[-1]
        prev_macd = macd_line.iloc[-2]
        prev_signal = signal_line.iloc[-2]
        prev_hist = histogram.iloc[-2]
        curr_close = close.iloc[-1]
        curr_atr = float(atr.iloc[-1])

        # ─ 골든크로스 (MACD가 Signal을 상향 돌파) ─
        golden_cross = (prev_macd <= prev_signal) and (curr_macd > curr_signal)
        # 히스토그램 가속 (모멘텀 강화)
        hist_accelerating = curr_hist > prev_hist
        # 제로선 위 (추가 확인)
        above_zero = curr_macd > 0

        if golden_cross and hist_accelerating:
            confidence = 0.65
            if above_zero:
                confidence = 0.80      # 제로선 위 크로스: 더 강한 신호
            score = min(abs(curr_hist / curr_close) * 1000, 1.0)
            return self._create_signal(
                signal=SignalType.BUY,
                score=score,
                confidence=confidence,
                market=market,
                entry_price=curr_close,
                stop_loss=curr_close - curr_atr * 1.5,
                take_profit=curr_close + curr_atr * 3.0,
                reason=f"MACD 골든크로스 | MACD:{curr_macd:.4f} > Signal:{curr_signal:.4f}",
                timeframe=timeframe,
                metadata={"macd": curr_macd, "signal": curr_signal, "histogram": curr_hist}
            )

        # ─ 데드크로스 (MACD가 Signal을 하향 돌파) ─
        dead_cross = (prev_macd >= prev_signal) and (curr_macd < curr_signal)
        hist_decelerating = curr_hist < prev_hist
        below_zero = curr_macd < 0

        if dead_cross and hist_decelerating:
            confidence = 0.65
            if below_zero:
                confidence = 0.80
            score = -min(abs(curr_hist / curr_close) * 1000, 1.0)
            return self._create_signal(
                signal=SignalType.SELL,
                score=score,
                confidence=confidence,
                market=market,
                entry_price=curr_close,
                stop_loss=curr_close + curr_atr * 1.5,
                take_profit=curr_close - curr_atr * 3.0,
                reason=f"MACD 데드크로스 | MACD:{curr_macd:.4f} < Signal:{curr_signal:.4f}",
                timeframe=timeframe,
                metadata={"macd": curr_macd, "signal": curr_signal, "histogram": curr_hist}
            )

        return None
