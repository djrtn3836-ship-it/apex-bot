# strategies/trend_following.py
import pandas as pd
import numpy as np
from typing import Optional
from .base_strategy import BaseStrategy
from signals.signal_combiner import CombinedSignal, SignalType


class TrendFollowing(BaseStrategy):
    """- EMA  + ADX"""

    def __init__(self, settings: dict = None):
        super().__init__("TrendFollowing", settings)
        self.fast_ema = self.settings.get("fast_ema", 9)
        self.slow_ema = self.settings.get("slow_ema", 21)
        self.adx_period = self.settings.get("adx_period", 14)
        self.adx_threshold = self.settings.get("adx_threshold", 25)

    def analyze(self, market: str, df: pd.DataFrame, additional_data: dict = None) -> Optional[CombinedSignal]:
        if not self._validate_df(df, min_rows=self.slow_ema + self.adx_period + 5):
            return None

        close = df["close"].values
        high = df["high"].values
        low = df["low"].values

        ema_fast = self._ema(close, self.fast_ema)
        ema_slow = self._ema(close, self.slow_ema)
        adx = self._calc_adx(high, low, close, self.adx_period)

        if ema_fast is None or ema_slow is None or adx is None:
            return None

        # 추세 강도 확인
        if adx < self.adx_threshold:
            return None

        score = 0.0
        signal_type = None

        ema_fast_prev = self._ema(close[:-1], self.fast_ema)
        ema_slow_prev = self._ema(close[:-1], self.slow_ema)

        if ema_fast_prev is None or ema_slow_prev is None:
            return None

        adx_factor = min((adx - self.adx_threshold) / 25.0, 0.3)

        # 골든 크로스 (BUY)
        if ema_fast_prev <= ema_slow_prev and ema_fast > ema_slow:
            score = self._normalize_score(0.60 + adx_factor)
            signal_type = SignalType.BUY

        # 데드 크로스 (SELL)
        elif ema_fast_prev >= ema_slow_prev and ema_fast < ema_slow:
            score = self._normalize_score(0.60 + adx_factor)
            signal_type = SignalType.SELL

        # 추세 지속 (기존 방향 유지)
        elif ema_fast > ema_slow * 1.002:
            score = self._normalize_score(0.55 + adx_factor * 0.5)
            signal_type = SignalType.BUY

        elif ema_fast < ema_slow * 0.998:
            score = self._normalize_score(0.55 + adx_factor * 0.5)
            signal_type = SignalType.SELL

        if signal_type is None:
            return None

        return CombinedSignal(
            market=market,
            signal_type=signal_type,
            score=score,
            confidence=score * 0.90,
            strategies=["TrendFollowing"],
            regime="TREND",
            metadata={"ema_fast": ema_fast, "ema_slow": ema_slow, "adx": adx},
        )

    def _ema(self, close, period):
        if len(close) < period:
            return None
        k = 2.0 / (period + 1)
        ema = float(close[-period])
        for price in close[-period + 1:]:
            ema = price * k + ema * (1 - k)
        return ema

    def _calc_adx(self, high, low, close, period):
        if len(close) < period * 2:
            return None
        plus_dm, minus_dm, tr_list = [], [], []
        for i in range(1, len(close)):
            h_diff = high[i] - high[i - 1]
            l_diff = low[i - 1] - low[i]
            plus_dm.append(h_diff if h_diff > l_diff and h_diff > 0 else 0)
            minus_dm.append(l_diff if l_diff > h_diff and l_diff > 0 else 0)
            tr_list.append(max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            ))
        if len(tr_list) < period:
            return None
        atr = np.mean(tr_list[-period:])
        if atr == 0:
            return None
        plus_di = 100 * np.mean(plus_dm[-period:]) / atr
        minus_di = 100 * np.mean(minus_dm[-period:]) / atr
        di_sum = plus_di + minus_di
        if di_sum == 0:
            return 0.0
        dx = 100 * abs(plus_di - minus_di) / di_sum
        return float(dx)
