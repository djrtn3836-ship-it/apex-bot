# strategies/rsi_divergence.py
import pandas as pd
import numpy as np
from typing import Optional
from .base_strategy import BaseStrategy
from signals.signal_combiner import CombinedSignal, SignalType


class RSIDivergence(BaseStrategy):
    """RSI   -  RSI"""

    def __init__(self, settings: dict = None):
        super().__init__("RSIDivergence", settings)
        self.rsi_period = self.settings.get("rsi_period", 14)
        self.lookback = self.settings.get("lookback", 10)
        self.rsi_low = self.settings.get("rsi_low", 35)
        self.rsi_high = self.settings.get("rsi_high", 65)

    def analyze(self, market: str, df: pd.DataFrame, additional_data: dict = None) -> Optional[CombinedSignal]:
        min_rows = self.rsi_period + self.lookback + 5
        if not self._validate_df(df, min_rows=min_rows):
            return None

        close = df["close"].values
        rsi_series = self._calc_rsi_series(close, self.rsi_period)
        if rsi_series is None or len(rsi_series) < self.lookback:
            return None

        price_window = close[-(self.lookback):]
        rsi_window = rsi_series[-(self.lookback):]

        current_rsi = rsi_window[-1]
        score = 0.0
        signal_type = None

        # 강세 다이버전스: 가격은 저점 낮아지나 RSI 저점은 높아짐 → BUY
        if current_rsi < self.rsi_low + 10:
            price_min_idx = np.argmin(price_window[:-1])
            rsi_min_idx = np.argmin(rsi_window[:-1])
            if (price_window[-1] < price_window[price_min_idx] and
                    rsi_window[-1] > rsi_window[rsi_min_idx] + 2):
                divergence_strength = (rsi_window[-1] - rsi_window[rsi_min_idx]) / (self.rsi_low + 10)
                score = self._normalize_score(0.58 + divergence_strength * 0.25)
                signal_type = SignalType.BUY

        # 약세 다이버전스: 가격은 고점 높아지나 RSI 고점은 낮아짐 → SELL
        elif current_rsi > self.rsi_high - 10:
            price_max_idx = np.argmax(price_window[:-1])
            rsi_max_idx = np.argmax(rsi_window[:-1])
            if (price_window[-1] > price_window[price_max_idx] and
                    rsi_window[-1] < rsi_window[rsi_max_idx] - 2):
                divergence_strength = (rsi_window[rsi_max_idx] - rsi_window[-1]) / (100 - self.rsi_high + 10)
                score = self._normalize_score(0.58 + divergence_strength * 0.25)
                signal_type = SignalType.SELL

        if signal_type is None:
            return None

        return CombinedSignal(
            market=market,
            signal_type=signal_type,
            score=score,
            confidence=score * 0.88,
            strategies=["RSIDivergence"],
            regime="DIVERGENCE",
            metadata={"rsi": current_rsi, "lookback": self.lookback},
        )

    def _calc_rsi_series(self, close, period):
        if len(close) < period + 2:
            return None
        rsi_vals = []
        for i in range(period, len(close)):
            segment = close[i - period: i + 1]
            deltas = np.diff(segment)
            gains = np.where(deltas > 0, deltas, 0.0)
            losses = np.where(deltas < 0, -deltas, 0.0)
            avg_gain = np.mean(gains)
            avg_loss = np.mean(losses)
            if avg_loss == 0:
                rsi_vals.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_vals.append(100.0 - 100.0 / (1.0 + rs))
        return np.array(rsi_vals)
