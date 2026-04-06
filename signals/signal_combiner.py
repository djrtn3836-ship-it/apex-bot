"""
APEX BOT - 신호 결합기
멀티 전략 신호 → 앙상블 최종 신호 생성

수정 이력:
  v1.1 - _filter_by_regime() Signal 생성 인수 불일치 수정
         (signal_type= → signal=, strength= → score=)
       - 부스트 신호 생성 헬퍼 _boost_signal() 추가
"""
import asyncio
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import pandas as pd
from loguru import logger

from strategies.base_strategy import StrategySignal as Signal, SignalType
from config.settings import get_settings


@dataclass
class CombinedSignal:
    """앙상블 결합 최종 신호"""
    market: str
    signal_type: SignalType
    score: float
    confidence: float
    agreement_rate: float
    contributing_strategies: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)
    ml_signal: Optional[str] = None
    ml_confidence: float = 0.0


class SignalCombiner:
    """
    멀티 전략 신호 결합 엔진

    가중치 체계:
    - ML 앙상블 (LSTM+TFT+CNN): 가중치 2.5
    - 시장구조 (OB/SMC): 1.5
    - 모멘텀 (MACD/RSI/Supertrend): 1.0~1.5
    - 변동성 돌파 (ATR/볼린저): 1.0
    - 평균회귀 (VWAP/BB): 1.2
    """

    STRATEGY_WEIGHTS = {
        # ── 모멘텀 전략 ──────────────────────────────
        # MACD_Cross: 백테스트 +2.2% → 1.8 상향
        "MACD_Cross":        1.8,
        # RSI_Divergence: 백테스트 -10.0% → 0.0 완전 차단
        "RSI_Divergence":    0.0,
        # Supertrend: 백테스트 trend_following +2.0% → 1.3 유지
        "Supertrend":        1.3,
        # ── 평균회귀 전략 ─────────────────────────────
        "Bollinger_Squeeze": 1.0,
        "VWAP_Reversion":    1.2,
        # ── 변동성 전략 ──────────────────────────────
        # VolBreakout: 백테스트 -2.7% → 0.4로 하향
        "VolBreakout":       0.4,
        "ATR_Channel":       1.0,
        # ── 시장구조 전략 ─────────────────────────────
        # OrderBlock_SMC: 백테스트 -4.7% → 0.3으로 하향
        "OrderBlock_SMC":    0.3,
        # ── ML/AI 레이어 ─────────────────────────────
        # ML_Ensemble: 백테스트 +2.9% → 3.0으로 상향 (핵심 전략)
        "ML_Ensemble":       3.0,
        "BEAR_REVERSAL":     2.0,
    }

    # 레짐별 선호 전략
    REGIME_PREFERRED = {
        "TRENDING":   {"MACD_Cross", "Supertrend", "OrderBlock_SMC", "VolBreakout"},
        "RANGING":    {"VWAP_Reversion", "Bollinger_Squeeze", "RSI_Divergence", "ATR_Channel"},
        "VOLATILE":   {"VolBreakout", "ATR_Channel", "Bollinger_Squeeze"},
        "BEAR_REVERSAL": {"RSI_Divergence", "VWAP_Reversion", "Bollinger_Squeeze"},
    }

    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.buy_threshold  = self.settings.risk.buy_signal_threshold
        self.sell_threshold = self.settings.risk.sell_signal_threshold
        self.min_agreement  = 0.20  # 완화

    # ── 신호 결합 ─────────────────────────────────────────────

    def combine(
        self,
        signals: List[Signal],
        market: str,
        ml_prediction: Optional[Dict] = None,
        regime: str = "UNKNOWN",
    ) -> Optional[CombinedSignal]:
        """다중 신호 결합 → 최종 신호 반환"""
        if not signals and not ml_prediction:
            return None

        ml_signal     = None
        ml_confidence = 0.0
        if ml_prediction:
            ml_signal     = ml_prediction.get("signal", "HOLD")
            ml_confidence = ml_prediction.get("confidence", 0.0)

        filtered_signals = self._filter_by_regime(signals, regime)

        buy_score  = 0.0
        sell_score = 0.0
        buy_strategies  = []
        sell_strategies = []
        reasons = []

        for sig in filtered_signals:
            weight           = self.STRATEGY_WEIGHTS.get(sig.strategy_name, 1.0)
            weighted_strength = sig.score * weight * sig.confidence

            if sig.signal == SignalType.BUY:
                buy_score += weighted_strength
                buy_strategies.append(sig.strategy_name)
                reasons.append(sig.reason)
            elif sig.signal == SignalType.SELL:
                sell_score += weighted_strength
                sell_strategies.append(sig.strategy_name)
                reasons.append(sig.reason)

        # ML 신호 점수 추가
        if ml_signal and ml_confidence > 0.50:  # ML 단독 매수 방지
            ml_weight = self.STRATEGY_WEIGHTS["ML_Ensemble"]
            if ml_signal == "BUY":
                buy_score += ml_weight * ml_confidence
                buy_strategies.append("ML_Ensemble")
            elif ml_signal == "SELL":
                sell_score += ml_weight * ml_confidence
                sell_strategies.append("ML_Ensemble")

        total_strategies = len(filtered_signals) + (1 if ml_signal else 0)
        net_score        = buy_score - sell_score

        if net_score >= self.buy_threshold:
            n_buy          = len(buy_strategies)
            agreement_rate = n_buy / max(total_strategies, 1)
            # 동의율 체크 비활성화 — 단일 전략 신호도 허용
            if agreement_rate < self.min_agreement:
                return None  # BUY 동의율 미달 → HOLD

            avg_confidence = (
                sum(s.confidence for s in filtered_signals
                    if s.signal == SignalType.BUY)
                / max(n_buy, 1)
            )
            return CombinedSignal(
                market=market,
                signal_type=SignalType.BUY,
                score=buy_score,
                confidence=avg_confidence,
                agreement_rate=agreement_rate,
                contributing_strategies=buy_strategies,
                reasons=reasons[:5],
                metadata={
                    "buy_score": buy_score,
                    "sell_score": sell_score,
                    "regime": regime,
                },
                ml_signal=ml_signal,
                ml_confidence=ml_confidence,
            )

        elif net_score <= self.sell_threshold:
            n_sell         = len(sell_strategies)
            agreement_rate = n_sell / max(total_strategies, 1)
            # SELL 신호 품질 검증
            if agreement_rate < self.min_agreement and not (
                ml_signal == 'SELL' and ml_confidence > 0.55
            ):
                return None  # SELL 동의율 미달 → HOLD

            avg_confidence = (
                sum(s.confidence for s in filtered_signals
                    if s.signal == SignalType.SELL)
                / max(n_sell, 1)
            )
            return CombinedSignal(
                market=market,
                signal_type=SignalType.SELL,
                score=-sell_score,
                confidence=avg_confidence,
                agreement_rate=agreement_rate,
                contributing_strategies=sell_strategies,
                reasons=reasons[:5],
                metadata={
                    "buy_score": buy_score,
                    "sell_score": sell_score,
                    "regime": regime,
                },
                ml_signal=ml_signal,
                ml_confidence=ml_confidence,
            )

        return None  # HOLD

    # ── 레짐 필터 ─────────────────────────────────────────────

    def _filter_by_regime(
        self, signals: List[Signal], regime: str
    ) -> List[Signal]:
        """
        시장 레짐에 맞는 전략만 필터링 + 선호 전략 1.2배 부스트

        ✅ FIX: Signal 생성 시 signal= (not signal_type=),
                score= (not strength=) 사용
        """
        preferred = self.REGIME_PREFERRED.get(regime.upper(), None)
        if preferred is None:
            return signals  # 알 수 없는 레짐 → 필터 없음

        boosted = []
        for sig in signals:
            if sig.strategy_name in preferred:
                boosted.append(
                    self._boost_signal(sig, score_mult=1.2, reason_suffix="[레짐부스트]")
                )
            else:
                boosted.append(sig)
        return boosted

    @staticmethod
    def _boost_signal(sig: Signal, score_mult: float = 1.0,
                      reason_suffix: str = "") -> Signal:
        """
        ✅ FIX: StrategySignal 실제 필드명(signal, score)으로 새 신호 생성
        """
        from strategies.base_strategy import StrategySignal
        return StrategySignal(
            strategy_name=sig.strategy_name,
            market=sig.market,
            signal=sig.signal,                         # ✅ signal= (not signal_type=)
            score=min(sig.score * score_mult, 1.0),    # ✅ score= (not strength=)
            confidence=sig.confidence,
            entry_price=sig.entry_price,
            stop_loss=sig.stop_loss,
            take_profit=sig.take_profit,
            reason=sig.reason + reason_suffix,
            timeframe=sig.timeframe,
            timestamp=sig.timestamp,
            metadata=sig.metadata,
        )

    def get_score_breakdown(self, signals: List[Signal]) -> Dict:
        """신호 점수 상세 내역"""
        breakdown = {}
        for sig in signals:
            weight = self.STRATEGY_WEIGHTS.get(sig.strategy_name, 1.0)
            breakdown[sig.strategy_name] = {
                "type":           sig.signal.name,
                "strength":       sig.score,
                "confidence":     sig.confidence,
                "weight":         weight,
                "weighted_score": sig.score * weight * sig.confidence,
            }
        return breakdown
