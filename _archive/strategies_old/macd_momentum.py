# strategies/macd_momentum.py
import pandas as pd
import numpy as np
from typing import Optional
from .base_strategy import BaseStrategy
from signals.signal_combiner import CombinedSignal, SignalType


class MACDMomentum(BaseStrategy):
    """MACD   - MACD  +"""

    def __init__(self, settings: dict = None):
        super().__init__("MACDMomentum", settings)
        self.fast = self.settings.get("fast", 12)
        self.slow = self.settings.get("slow", 26)
        self.signal_period = self.settings.get("signal_period", 9)

    def analyze(self, market: str, df: pd.DataFrame, additional_data: dict = None) -> Optional[CombinedSignal]:
        min_rows = self.slow + self.signal_period + 5
        if not self._validate_df(df, min_rows=min_rows):
            return None

        close = df["close"].values

        macd_line, signal_line, histogram = self._calc_macd(close)
        if macd_line is None:
            return None

        score = 0.0
        signal_type = None

        hist_curr = histogram[-1]
        hist_prev = histogram[-2]
        macd_curr = macd_line[-1]
        signal_curr = signal_line[-1]
        macd_prev = macd_line[-2]
        signal_prev = signal_line[-2]

        price_range = np.std(close[-self.slow:]) + 1e-9

        # BUY: MACD가 시그널 위로 교차 + 히스토그램 양수로 전환
        if macd_prev <= signal_prev and macd_curr > signal_curr and hist_curr > 0:
            strength = abs(hist_curr) / price_range
            score = self._normalize_score(0.60 + min(strength * 10, 0.25))
            signal_type = SignalType.BUY

        # SELL: MACD가 시그널 아래로 교차 + 히스토그램 음수로 전환
        elif macd_prev >= signal_prev and macd_curr < signal_curr and hist_curr < 0:
            strength = abs(hist_curr) / price_range
            score = self._normalize_score(0.60 + min(strength * 10, 0.25))
            signal_type = SignalType.SELL

        # 히스토그램 연속 증가 (추세 가속)
        elif hist_curr > hist_prev > 0 and macd_curr > signal_curr:
            score = self._normalize_score(0.55 + min(abs(hist_curr) / price_range * 8, 0.20))
            signal_type = SignalType.BUY

        elif hist_curr < hist_prev < 0 and macd_curr < signal_curr:
            score = self._normalize_score(0.55 + min(abs(hist_curr) / price_range * 8, 0.20))
            signal_type = SignalType.SELL

        if signal_type is None:
            return None

        return CombinedSignal(
            market=market,
            signal_type=signal_type,
            score=score,
            confidence=score * 0.92,
            strategies=["MACDMomentum"],
            regime="MOMENTUM",
            metadata={"macd": macd_curr, "signal": signal_curr, "histogram": hist_curr},
        )

    def _ema(self, data, period):
        if len(data) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = float(data[0])
        for v in data[1:]:
            ema_val = v * k + ema_val * (1 - k)
        return ema_val

    def _calc_macd(self, close):
        if len(close) < self.slow + self.signal_period:
            return None, None, None
        ema_fast_series = self._ema_series(close, self.fast)
        ema_slow_series = self._ema_series(close, self.slow)
        if ema_fast_series is None or ema_slow_series is None:
            return None, None, None
        min_len = min(len(ema_fast_series), len(ema_slow_series))
        macd_series = ema_fast_series[-min_len:] - ema_slow_series[-min_len:]
        if len(macd_series) < self.signal_period:
            return None, None, None
        signal_series = self._ema_series(macd_series, self.signal_period)
        if signal_series is None:
            return None, None, None
        min_len2 = min(len(macd_series), len(signal_series))
        hist = macd_series[-min_len2:] - signal_series[-min_len2:]
        return macd_series[-min_len2:], signal_series[-min_len2:], hist

    def _ema_series(self, data, period):
        if len(data) < period:
            return None
        k = 2.0 / (period + 1)
        result = [float(np.mean(data[:period]))]
        for v in data[period:]:
            result.append(v * k + result[-1] * (1 - k))
        return np.array(result)
