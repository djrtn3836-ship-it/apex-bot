# strategies/volatility_breakout.py
import pandas as pd
import numpy as np
from typing import Optional
from .base_strategy import BaseStrategy
from signals.signal_combiner import CombinedSignal, SignalType


class VolatilityBreakout(BaseStrategy):
    """변동성 돌파 전략 - Larry Williams 방식"""

    def __init__(self, settings: dict = None):
        super().__init__("VolatilityBreakout", settings)
        self.k = self.settings.get("k", 0.5)
        self.atr_period = self.settings.get("atr_period", 14)
        self.volume_factor = self.settings.get("volume_factor", 1.2)

    def analyze(self, market: str, df: pd.DataFrame, additional_data: dict = None) -> Optional[CombinedSignal]:
        if not self._validate_df(df, min_rows=30):
            return None

        additional_data = additional_data or {}
        fear_greed = additional_data.get("fear_greed", 50)

        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        volume = df["volume"].values

        # ATR 계산
        atr = self._calc_atr(high, low, close, self.atr_period)
        if atr is None or atr == 0:
            return None

        # 변동성 돌파 기준선
        prev_range = high[-2] - low[-2]
        target = close[-2] + self.k * prev_range

        current_price = close[-1]
        current_volume = volume[-1]
        avg_volume = np.mean(volume[-20:-1])

        score = 0.0
        signal_type = None

        # BUY 조건: 가격이 돌파 기준 초과 + 거래량 확인
        if current_price > target and avg_volume > 0:
            volume_ratio = current_volume / avg_volume
            if volume_ratio >= self.volume_factor:
                # 점수 계산
                breakout_strength = (current_price - target) / atr
                score = self._normalize_score(0.5 + min(breakout_strength * 0.2, 0.4))
                # Fear & Greed 보정 (극도 공포 시 점수 약간 하향)
                if fear_greed < 20:
                    score *= 0.85
                if score >= 0.5:
                    signal_type = SignalType.BUY

        # SELL 조건: 가격이 전일 저점 아래로 이탈
        elif current_price < low[-2] - self.k * prev_range:
            drop_strength = (low[-2] - current_price) / atr
            score = self._normalize_score(0.5 + min(drop_strength * 0.2, 0.4))
            if score >= 0.5:
                signal_type = SignalType.SELL

        if signal_type is None:
            return None

        confidence = self._normalize_score(score * (1 + (volume_ratio - 1) * 0.1) if signal_type == SignalType.BUY else score)

        return CombinedSignal(
            market=market,
            signal_type=signal_type,
            score=score,
            confidence=confidence,
            strategies=["VolatilityBreakout"],
            regime="BREAKOUT",
            metadata={"target": target, "atr": atr, "k": self.k},
        )

    def _calc_atr(self, high, low, close, period):
        if len(close) < period + 1:
            return None
        trs = []
        for i in range(1, len(close)):
            tr = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )
            trs.append(tr)
        if len(trs) < period:
            return None
        return float(np.mean(trs[-period:]))
