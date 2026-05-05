"""APEX BOT -  
   →    

 :
  v1.1 - _filter_by_regime() Signal    
         (signal_type= → signal=, strength= → score=)
       -     _boost_signal() 
  v1.2 - RSI_Divergence  0.0 → REGIME_PREFERRED  
         ( 0.0    0.0 × 1.2 = 0.0   )"""
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
    """CombinedSignal 클래스"""
    market: str
    signal_type: SignalType
    score: float
    confidence: float
    agreement_rate: float
    contributing_strategies: List[str] = field(default_factory=list)

    # ── strategy_name 하위호환 property ──────────────────────
    @property
    def strategy_name(self) -> str:
        """contributing_strategies[0] 반환 (하위호환용)"""
        return self.contributing_strategies[0] if self.contributing_strategies else ""

    reasons: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)
    ml_signal: Optional[str] = None
    ml_confidence: float = 0.0

    # ── dict-like compatibility (하위호환) ──────────────────
    def get(self, key: str, default=None):
        """dict.get() 호환 - CombinedSignal을 dict처럼 사용하는 코드 지원"""
        return getattr(self, key, default)

    def __getitem__(self, key: str):
        """dict[] 접근 호환"""
        val = getattr(self, key, None)
        if val is None:
            raise KeyError(key)
        return val

    def __contains__(self, key: str) -> bool:
        """'key' in signal 호환"""
        return hasattr(self, key)


class SignalCombiner:
    """:
    - ML  (LSTM+TFT+CNN):  3.0
    -   (BEAR_REVERSAL): 2.0
    -  (MACD/Supertrend): 1.3~1.8
    -  (VWAP/BB): 1.0~1.2
    -   (ATR/): 0.4~1.0
    -  (OB/SMC): 0.3
    - RSI_Divergence: 0.0 ( -10.0% →  )"""

    STRATEGY_WEIGHTS = {
        # ── 모멘텀 전략 ──────────────────────────────────────
        # MACD_Cross: 백테스트 +2.2% → 1.8 상향
        "Vol_Breakout":     0.6,  # [FIX] 단독손실 전략 낮은 가중치
        "MACD_Cross":        1.8,
        # RSI_Divergence: 백테스트 -10.0% → 0.0 완전 차단
        "RSI_Divergence":    0.0,
        # Supertrend: 백테스트 +2.0% → 1.3 유지
        "Supertrend":        1.3,
        # ── 평균회귀 전략 ─────────────────────────────────────
        "Bollinger_Squeeze": 1.4,  # [FIX] 급등포착 상향 1.0->1.4
        # [ST-1] "VWAP_Reversion": 0.7,  # 비활성화: -₩3,158
        # ── 변동성 전략 ──────────────────────────────────────
        # VolBreakout: 백테스트 -2.7% → 0.4로 하향
        "VolBreakout":       0.4,
        "ATR_Channel":       1.0,
        # ── 시장구조 전략 ─────────────────────────────────────
        # OrderBlock_SMC: 백테스트 -4.7% → 0.3으로 하향
        "OrderBlock_SMC":    0.3,
        # ── ML/AI 레이어 ─────────────────────────────────────
        # ML_Ensemble: 백테스트 +2.9% → 3.0으로 상향 (핵심 전략)
        "ML_Ensemble":       3.0,
        "BEAR_REVERSAL":     2.0,
    }

    # ✅ FIX v1.2: RSI_Divergence 제거
    # 가중치가 0.0인 전략을 레짐 부스트(×1.2)해도 0.0이므로
    # REGIME_PREFERRED 포함 자체가 의미 없고 코드 혼란만 유발
    REGIME_PREFERRED = {
        "TRENDING": {
            "MACD_Cross",
            "Supertrend",
            "OrderBlock_SMC",
            "VolBreakout",
        },
        "RANGING": {
            "VWAP_Reversion",
            "Bollinger_Squeeze",
            # RSI_Divergence 제거 — 가중치 0.0, 비활성화 전략
            "ATR_Channel",
        },
        "VOLATILE": {
            "VolBreakout",
            "ATR_Channel",
            "Bollinger_Squeeze",
        },
        "BEAR_REVERSAL": {
            # RSI_Divergence 제거 — 가중치 0.0, 비활성화 전략
            "VWAP_Reversion",
            "Bollinger_Squeeze",
            "BEAR_REVERSAL",
        },
    }

    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.buy_threshold  = self.settings.risk.buy_signal_threshold  # BUG-1 FIX: min(0.35) 제거
        self.sell_threshold = -self.settings.risk.sell_signal_threshold  # BUG-1 FIX: max(-0.35) 제거
        self.min_agreement  = 0.20  # 단일 전략 신호도 허용

    # ── 신호 결합 ────────────────────────────────────────────────

    def combine(
        self,
        signals: List[Signal],
        market: str,
        ml_prediction: Optional[Dict] = None,
        regime: str = "UNKNOWN",
    ) -> Optional[CombinedSignal]:
        """→"""
        if not signals and not ml_prediction:
            return None

        ml_signal     = None
        ml_confidence = 0.0
        if ml_prediction:
            ml_signal     = ml_prediction.get("signal", "HOLD")
            ml_confidence = ml_prediction.get("confidence", 0.0)

        filtered_signals = self._filter_by_regime(signals, regime)

        buy_score        = 0.0
        sell_score       = 0.0
        buy_strategies   = []
        sell_strategies  = []
        reasons          = []

        for sig in filtered_signals:
            weight            = self.STRATEGY_WEIGHTS.get(sig.strategy_name, 1.0)
            weighted_strength = sig.score * weight * sig.confidence

            if sig.signal == SignalType.BUY:
                buy_score += weighted_strength
                buy_strategies.append(sig.strategy_name)
                reasons.append(sig.reason)
            elif sig.signal == SignalType.SELL:
                sell_score += weighted_strength
                sell_strategies.append(sig.strategy_name)
                reasons.append(sig.reason)

        # ML 신호 점수 추가 (confidence > 0.50 조건 유지)
        if ml_signal and ml_confidence > 0.45:
            ml_weight = self.STRATEGY_WEIGHTS["ML_Ensemble"]
            if ml_signal == "BUY":
                buy_score += ml_weight * ml_confidence
                buy_strategies.append("ML_Ensemble")
            elif ml_signal == "SELL":
                sell_score += ml_weight * ml_confidence
                sell_strategies.append("ML_Ensemble")
            elif ml_signal == "HOLD" and len(buy_strategies) >= 3:
                # [FIX] ML=HOLD 이지만 전략 BUY 3개 이상 → 절반 가중치로 BUY 보정
                buy_score += ml_weight * ml_confidence * 0.5
                buy_strategies.append("ML_HOLD_BOOST")

        total_strategies = len(filtered_signals) + (1 if ml_signal else 0)
        net_score        = buy_score - sell_score

        if net_score >= self.buy_threshold:
            n_buy          = len(buy_strategies)
            agreement_rate = n_buy / max(total_strategies, 1)
            if agreement_rate < self.min_agreement:
                return None  # BUY 동의율 미달 → HOLD

            avg_confidence = (
                sum(s.confidence for s in filtered_signals
                    if s.signal == SignalType.BUY)
                / max(n_buy, 1)
            )
            if avg_confidence < 0.01 and ml_confidence > 0.0:
                avg_confidence = ml_confidence  # ML confidence fallback (BUY)
            return CombinedSignal(
                market=market,
                signal_type=SignalType.BUY,
                score=buy_score,
                confidence=avg_confidence,
                agreement_rate=agreement_rate,
                contributing_strategies=buy_strategies,
                reasons=reasons[:5],
                metadata={
                    "buy_score":  buy_score,
                    "sell_score": sell_score,
                    "regime":     regime,
                },
                ml_signal=ml_signal,
                ml_confidence=ml_confidence,
            )

        elif net_score <= self.sell_threshold:
            n_sell         = len(sell_strategies)
            agreement_rate = n_sell / max(total_strategies, 1)
            if agreement_rate < self.min_agreement and not (
                ml_signal == "SELL" and ml_confidence > 0.52
            ):
                return None  # SELL 동의율 미달 → HOLD

            avg_confidence = (
                sum(s.confidence for s in filtered_signals
                    if s.signal == SignalType.SELL)
                / max(n_sell, 1)
            )
            if avg_confidence < 0.01 and ml_confidence > 0.0:
                avg_confidence = ml_confidence  # ML confidence fallback (SELL)
            return CombinedSignal(
                market=market,
                signal_type=SignalType.SELL,
                score=-sell_score,
                confidence=avg_confidence,
                agreement_rate=agreement_rate,
                contributing_strategies=sell_strategies,
                reasons=reasons[:5],
                metadata={
                    "buy_score":  buy_score,
                    "sell_score": sell_score,
                    "regime":     regime,
                },
                ml_signal=ml_signal,
                ml_confidence=ml_confidence,
            )

        return None  # HOLD

    # ── 레짐 필터 ────────────────────────────────────────────────

    def _filter_by_regime(
        self, signals: List[Signal], regime: str
    ) -> List[Signal]:
        """+   1.2 

         FIX v1.1: Signal   signal= (not signal_type=),
                     score= (not strength=) 
         FIX v1.2: RSI_Divergence REGIME_PREFERRED 
                     preferred"""
        preferred = self.REGIME_PREFERRED.get(regime.upper(), None)
        if preferred is None:
            return signals  # 알 수 없는 레짐 → 필터 없음

        boosted = []
        for sig in signals:
            if sig.strategy_name in preferred:
                boosted.append(
                    self._boost_signal(
                        sig,
                        score_mult=1.2,
                        reason_suffix="[레짐부스트]",
                    )
                )
            else:
                boosted.append(sig)
        return boosted

    @staticmethod
    def _boost_signal(
        sig: Signal,
        score_mult: float = 1.0,
        reason_suffix: str = "",
    ) -> Signal:
        """FIX v1.1: StrategySignal  (signal, score)"""
        from strategies.base_strategy import StrategySignal
        return StrategySignal(
            strategy_name=sig.strategy_name,
            market=sig.market,
            signal=sig.signal,                       # ✅ signal= (not signal_type=)
            score=min(sig.score * score_mult, 1.0),  # ✅ score= (not strength=)
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
        """get_score_breakdown 실행"""
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
