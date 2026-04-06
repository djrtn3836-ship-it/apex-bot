# strategies/volume_spike.py
import pandas as pd
import numpy as np
from typing import Optional
from .base_strategy import BaseStrategy
from signals.signal_combiner import CombinedSignal, SignalType


class VolumeSpike(BaseStrategy):
    """거래량 급등 전략 - 비정상적 거래량 + 가격 방향 확인"""

    def __init__(self, settings: dict = None):
        super().__init__("VolumeSpike", settings)
        self.volume_period = self.settings.get("volume_period", 20)
        self.spike_multiplier = self.settings.get("spike_multiplier", 2.0)
        self.price_change_threshold = self.settings.get("price_change_threshold", 0.005)

    def analyze(self, market: str, df: pd.DataFrame, additional_data: dict = None) -> Optional[CombinedSignal]:
        if not self._validate_df(df, min_rows=self.volume_period + 5):
            return None

        close = df["close"].values
        volume = df["volume"].values
        open_price = df["open"].values

        current_volume = volume[-1]
        avg_volume = np.mean(volume[-(self.volume_period + 1):-1])
        if avg_volume == 0:
            return None

        volume_ratio = current_volume / avg_volume

        # 거래량 급등 확인
        if volume_ratio < self.spike_multiplier:
            return None

        # 가격 방향 확인
        price_change = (close[-1] - open_price[-1]) / (open_price[-1] + 1e-9)
        prev_close_change = (close[-1] - close[-2]) / (close[-2] + 1e-9)

        score = 0.0
        signal_type = None

        volume_score = min((volume_ratio - self.spike_multiplier) / self.spike_multiplier * 0.3, 0.25)

        # 양봉 + 거래량 급등 → BUY
        if price_change > self.price_change_threshold and prev_close_change > 0:
            score = self._normalize_score(0.58 + volume_score + min(price_change * 5, 0.15))
            signal_type = SignalType.BUY

        # 음봉 + 거래량 급등 → SELL (분산 매도 신호)
        elif price_change < -self.price_change_threshold and prev_close_change < 0:
            score = self._normalize_score(0.58 + volume_score + min(abs(price_change) * 5, 0.15))
            signal_type = SignalType.SELL

        if signal_type is None:
            return None

        # Fear & Greed 보정
        fear_greed = (additional_data or {}).get("fear_greed", 50)
        if signal_type == SignalType.BUY and fear_greed < 20:
            score *= 0.90
        elif signal_type == SignalType.SELL and fear_greed > 80:
            score *= 0.90

        return CombinedSignal(
            market=market,
            signal_type=signal_type,
            score=score,
            confidence=score * 0.85,
            strategies=["VolumeSpike"],
            regime="VOLUME_SPIKE",
            metadata={
                "volume_ratio": volume_ratio,
                "price_change": price_change,
                "avg_volume": avg_volume,
            },
        )
