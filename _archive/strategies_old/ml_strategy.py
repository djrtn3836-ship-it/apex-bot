# strategies/ml_strategy.py
import pandas as pd
import numpy as np
from typing import Optional
from .base_strategy import BaseStrategy
from signals.signal_combiner import CombinedSignal, SignalType


class MLStrategy(BaseStrategy):
    """ML     - engine  ml_prediction"""

    def __init__(self, settings: dict = None):
        super().__init__("MLStrategy", settings)
        self.min_confidence = self.settings.get("min_confidence", 0.55)
        self.buy_threshold = self.settings.get("buy_threshold", 0.60)
        self.sell_threshold = self.settings.get("sell_threshold", 0.60)
        self.feature_lookback = self.settings.get("feature_lookback", 30)

    def analyze(self, market: str, df: pd.DataFrame, additional_data: dict = None) -> Optional[CombinedSignal]:
        if not self._validate_df(df, min_rows=self.feature_lookback):
            return None

        additional_data = additional_data or {}
        ml_prediction = additional_data.get("ml_prediction")

        # ml_prediction이 있으면 우선 사용
        if ml_prediction is not None:
            return self._signal_from_ml_prediction(market, ml_prediction, df, additional_data)

        # ml_prediction 없으면 기술적 지표 기반 대체 신호
        return self._signal_from_features(market, df, additional_data)

    def _signal_from_ml_prediction(self, market, ml_prediction, df, additional_data):
        """ML"""
        signal_type_str = None
        score = 0.0
        confidence = 0.0

        if isinstance(ml_prediction, dict):
            signal_type_str = ml_prediction.get("signal", "HOLD")
            confidence = float(ml_prediction.get("confidence", 0.0))
            score = float(ml_prediction.get("score", confidence))
        elif isinstance(ml_prediction, str):
            signal_type_str = ml_prediction
            confidence = 0.60
            score = 0.60
        else:
            return None

        if confidence < self.min_confidence:
            return None

        if signal_type_str == "BUY" and score >= self.buy_threshold:
            signal_type = SignalType.BUY
        elif signal_type_str == "SELL" and score >= self.sell_threshold:
            signal_type = SignalType.SELL
        else:
            return None

        # Fear & Greed 보정
        fear_greed = additional_data.get("fear_greed", 50)
        if signal_type == SignalType.BUY and fear_greed < 15:
            score = min(score + 0.03, 1.0)

        return CombinedSignal(
            market=market,
            signal_type=signal_type,
            score=self._normalize_score(score),
            confidence=self._normalize_score(confidence),
            strategies=["MLStrategy"],
            regime="ML_DRIVEN",
            metadata={"ml_prediction": ml_prediction, "fear_greed": fear_greed},
        )

    def _signal_from_features(self, market, df, additional_data):
        """ML       ()"""
        close = df["close"].values
        volume = df["volume"].values

        if len(close) < self.feature_lookback:
            return None

        # 특성 계산
        returns = np.diff(close[-self.feature_lookback:]) / (close[-self.feature_lookback:-1] + 1e-9)
        avg_return = np.mean(returns)
        std_return = np.std(returns) + 1e-9
        momentum = (close[-1] - close[-self.feature_lookback]) / (close[-self.feature_lookback] + 1e-9)
        volume_trend = np.mean(volume[-5:]) / (np.mean(volume[-self.feature_lookback:-5]) + 1e-9)

        # z-score 기반 단순 신호
        z_score = avg_return / std_return
        score = 0.0
        signal_type = None

        if z_score > 1.5 and momentum > 0.02 and volume_trend > 1.1:
            score = self._normalize_score(0.55 + min(z_score * 0.05, 0.15))
            signal_type = SignalType.BUY
        elif z_score < -1.5 and momentum < -0.02 and volume_trend > 1.1:
            score = self._normalize_score(0.55 + min(abs(z_score) * 0.05, 0.15))
            signal_type = SignalType.SELL

        if signal_type is None:
            return None

        return CombinedSignal(
            market=market,
            signal_type=signal_type,
            score=score,
            confidence=score * 0.80,
            strategies=["MLStrategy"],
            regime="ML_FALLBACK",
            metadata={"z_score": z_score, "momentum": momentum, "volume_trend": volume_trend},
        )
