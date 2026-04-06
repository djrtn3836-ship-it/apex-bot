"""
APEX BOT - ML 앙상블 전략 플러그인
ML 예측 결과를 전략 신호로 변환 (strategy layer 통합용)
"""
import pandas as pd
from typing import Optional
from loguru import logger

from strategies.base_strategy import BaseStrategy, Signal, SignalType


class MLEnsembleStrategy(BaseStrategy):
    """
    ML 앙상블 신호를 전략 시스템에 통합하는 어댑터
    - BiLSTM + TFT + CNN-LSTM 앙상블 예측 결과 사용
    - 신뢰도 임계값 이상일 때만 신호 생성
    - 3모델 동의율 필터 적용
    """

    def __init__(self, predictor=None):
        super().__init__(name="ML_Ensemble", weight=2.5)
        self._predictor = predictor
        self.min_confidence = 0.60      # 최소 신뢰도 60%
        self.min_agreement = 0.67       # 최소 3모델 동의율 2/3
        self._last_predictions = {}     # {market: prediction}

    def set_predictor(self, predictor):
        """ML 예측기 주입"""
        self._predictor = predictor

    def update_prediction(self, market: str, prediction: dict):
        """외부에서 ML 예측 결과 업데이트"""
        self._last_predictions[market] = prediction

    def analyze(self, market: str, df: pd.DataFrame,
                context: dict = None) -> Optional[Signal]:
        """ML 예측 결과 → Signal 변환"""
        # 캐시된 예측 사용 (비동기 ML 추론 결과)
        prediction = self._last_predictions.get(market)

        if not prediction:
            # 컨텍스트에서 ML 예측 가져오기
            if context:
                prediction = context.get("ml_prediction")

        if not prediction:
            return None

        signal_type_str = prediction.get("signal", "HOLD")
        confidence = prediction.get("confidence", 0.0)
        agreement = prediction.get("model_agreement", 0.0)
        buy_prob = prediction.get("buy_prob", 0.0)
        sell_prob = prediction.get("sell_prob", 0.0)

        # 임계값 필터
        if confidence < self.min_confidence:
            return None
        if agreement < self.min_agreement:
            return None
        if signal_type_str == "HOLD":
            return None

        # 신호 생성
        if signal_type_str == "BUY":
            return Signal(
                market=market,
                signal_type=SignalType.BUY,
                strength=buy_prob,
                confidence=confidence,
                strategy_name=self.name,
                reason=(
                    f"ML 앙상블 매수 | 확률={buy_prob:.2f} | "
                    f"신뢰도={confidence:.2f} | 동의율={agreement:.2f}"
                ),
                metadata={
                    "buy_prob": buy_prob,
                    "sell_prob": sell_prob,
                    "model_agreement": agreement,
                }
            )
        elif signal_type_str == "SELL":
            return Signal(
                market=market,
                signal_type=SignalType.SELL,
                strength=sell_prob,
                confidence=confidence,
                strategy_name=self.name,
                reason=(
                    f"ML 앙상블 매도 | 확률={sell_prob:.2f} | "
                    f"신뢰도={confidence:.2f}"
                ),
                metadata={
                    "buy_prob": buy_prob,
                    "sell_prob": sell_prob,
                    "model_agreement": agreement,
                }
            )
        return None

    def get_parameters(self) -> dict:
        return {
            "min_confidence": self.min_confidence,
            "min_agreement": self.min_agreement,
            "weight": self.weight,
        }
