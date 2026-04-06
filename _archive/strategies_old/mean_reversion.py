# strategies/mean_reversion.py
import pandas as pd
import numpy as np
from typing import Optional
from .base_strategy import BaseStrategy
from signals.signal_combiner import CombinedSignal, SignalType


class MeanReversion(BaseStrategy):
    """평균 회귀 전략 - 볼린저 밴드 + RSI 기반"""

    def __init__(self, settings: dict = None):
        super().__init__("MeanReversion", settings)
        self.bb_period = self.settings.get("bb_period", 20)
        self.bb_std = self.settings.get("bb_std", 2.0)
        self.rsi_period = self.settings.get("rsi_period", 14)
        self.rsi_oversold = self.settings.get("rsi_oversold", 30)
        self.rsi_overbought = self.settings.get("rsi_overbought", 70)

    def analyze(self, market: str, df: pd.DataFrame, additional_data: dict = None) -> Optional[CombinedSignal]:
        if not self._validate_df(df, min_rows=self.bb_period + self.rsi_period):
            return None

        close = df["close"].values

        # 볼린저 밴드
        sma = np.mean(close[-self.bb_period:])
        std = np.std(close[-self.bb_period:])
        upper = sma + self.bb_std * std
        lower = sma - self.bb_std * std

        # RSI
        rsi = self._calc_rsi(close, self.rsi_period)
        if rsi is None:
            return None

        current_price = close[-1]
        score = 0.0
        signal_type = None

        # BUY: 가격이 하단 밴드 근처 + RSI 과매도
        if current_price <= lower * 1.005 and rsi <= self.rsi_oversold + 5:
            distance_ratio = (lower - current_price) / (std + 1e-9)
            rsi_factor = (self.rsi_oversold + 5 - rsi) / (self.rsi_oversold + 5)
            score = self._normalize_score(0.55 + distance_ratio * 0.15 + rsi_factor * 0.20)
            signal_type = SignalType.BUY

        # SELL: 가격이 상단 밴드 근처 + RSI 과매수
        elif current_price >= upper * 0.995 and rsi >= self.rsi_overbought - 5:
            distance_ratio = (current_price - upper) / (std + 1e-9)
            rsi_factor = (rsi - (self.rsi_overbought - 5)) / (100 - self.rsi_overbought + 5)
            score = self._normalize_score(0.55 + distance_ratio * 0.15 + rsi_factor * 0.20)
            signal_type = SignalType.SELL

        if signal_type is None:
            return None

        # Fear & Greed 보정
        fear_greed = (additional_data or {}).get("fear_greed", 50)
        if signal_type == SignalType.BUY and fear_greed < 15:
            score = min(score + 0.05, 1.0)

        return CombinedSignal(
            market=market,
            signal_type=signal_type,
            score=score,
            confidence=score * 0.95,
            strategies=["MeanReversion"],
            regime="MEAN_REVERSION",
            metadata={"sma": sma, "upper": upper, "lower": lower, "rsi": rsi},
        )

    def _calc_rsi(self, close, period):
        if len(close) < period + 1:
            return None
        deltas = np.diff(close[-(period + 1):])
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))
