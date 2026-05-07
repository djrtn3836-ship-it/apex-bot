"""
signals/signal_combiner.py
─────────────────────────────────────────────────────────────
APEX BOT 신호 결합기

변경 이력:
  v1.0 - 초기 구현
  v1.1 - _filter_by_regime() Signal 필드명 수정
  v1.2 - RSI_Divergence REGIME_PREFERRED 제거
  v1.3 - [REFACTOR] VolBreakout 중복 키 제거, VWAP 명시 0.0,
          StrategyKey constants 참조
─────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import asyncio
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from loguru import logger

from strategies.base_strategy import StrategySignal as Signal, SignalType
from config.settings import get_settings
from core.constants import StrategyKey, DISABLED_STRATEGIES


@dataclass
class CombinedSignal:
    """결합 신호 데이터 클래스"""
    market: str
    signal_type: SignalType
    score: float
    confidence: float
    agreement_rate: float
    contributing_strategies: List[str] = field(default_factory=list)

    @property
    def strategy_name(self) -> str:
        return self.contributing_strategies[0] if self.contributing_strategies else ""

    reasons: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)
    ml_signal: Optional[str] = None
    ml_confidence: float = 0.0
    bear_reversal: bool = False   # [R3-PATCH] engine_buy BEAR_REVERSAL 플래그

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def __getitem__(self, key: str):
        val = getattr(self, key, None)
        if val is None:
            raise KeyError(key)
        return val

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)


class SignalCombiner:
    """
    전략 신호 결합기
    [REFACTOR v1.3] StrategyKey Enum 참조, 중복 키 제거
    """

    # ── 전략 가중치 ──────────────────────────────────────────
    # 규칙: 비활성화 전략은 0.0 으로 명시 (기본값 1.0 방지)
    STRATEGY_WEIGHTS: Dict[str, float] = {
        StrategyKey.MACD_CROSS:        1.8,
        StrategyKey.SUPERTREND:        1.3,
        StrategyKey.BOLLINGER_SQUEEZE: 1.4,
        StrategyKey.ATR_CHANNEL:       1.0,
        StrategyKey.ORDER_BLOCK_SMC:   1.0,   # [BSF-5] BULL 레짐 복원
        StrategyKey.ML_ENSEMBLE:       1.5,   # [P2-PATCH] 3.0→1.5: 기술전략 대비 균형 복원
        StrategyKey.BEAR_REVERSAL:     2.0,
    }

    # ── 레짐별 우선 전략 (1.2× 부스트) ──────────────────────
    # 비활성 전략(가중치 0.0)은 부스트해도 0.0이므로 제외
    REGIME_PREFERRED: Dict[str, set] = {
        "TRENDING": {
            StrategyKey.MACD_CROSS,
            StrategyKey.SUPERTREND,
            StrategyKey.ORDER_BLOCK_SMC,
        },
        "RANGING": {
            StrategyKey.BOLLINGER_SQUEEZE,
            StrategyKey.ATR_CHANNEL,
        },
        "VOLATILE": {
            StrategyKey.ATR_CHANNEL,
            StrategyKey.BOLLINGER_SQUEEZE,
        },
        "BEAR_REVERSAL": {
            StrategyKey.BOLLINGER_SQUEEZE,
            StrategyKey.BEAR_REVERSAL,
        },
        # [VP4-PATCH] GlobalRegime 키 매핑 추가
        "BULL": {
            StrategyKey.MACD_CROSS,
            StrategyKey.SUPERTREND,
            StrategyKey.BOLLINGER_SQUEEZE,
        },
        "RECOVERY": {
            StrategyKey.MACD_CROSS,
            StrategyKey.BOLLINGER_SQUEEZE,
        },
        "BEAR_WATCH": {
            StrategyKey.BOLLINGER_SQUEEZE,
            StrategyKey.ATR_CHANNEL,
        },
        "BEAR": {
            StrategyKey.BOLLINGER_SQUEEZE,
        },
    }

    def __init__(self, settings=None):
        self.settings       = settings or get_settings()
        # [U4-PATCH] buy_threshold: risk 설정(0.55) 대신 조정값 0.42 사용
        # 근거: weighted_strength = score×weight×confidence (단일전략 ≈0.50~0.65)
        _raw_thr = self.settings.risk.buy_signal_threshold  # 0.55
        self.buy_threshold  = max(0.35, _raw_thr * 0.80)  # 0.55×0.80=0.44
        self.sell_threshold = -self.settings.risk.sell_signal_threshold
        self.min_agreement  = 0.20

    def combine(
        self,
        signals: List[Signal],
        market: str,
        ml_prediction: Optional[Dict] = None,
        regime: str = "UNKNOWN",
    ) -> Optional[CombinedSignal]:

        if not signals and not ml_prediction:
            return None

        ml_signal     = None
        ml_confidence = 0.0
        if ml_prediction:
            ml_signal     = ml_prediction.get("signal", "HOLD")
            ml_confidence = ml_prediction.get("confidence", 0.0)

        filtered_signals = self._filter_by_regime(signals, regime)

        buy_score, sell_score   = 0.0, 0.0
        buy_strategies          = []
        sell_strategies         = []
        reasons                 = []

        for sig in filtered_signals:
            # [FIX] 비활성화 전략 신호 차단 (가중치 0.0 또는 DISABLED_STRATEGIES)
            if sig.strategy_name in DISABLED_STRATEGIES:
                continue
            weight            = self.STRATEGY_WEIGHTS.get(sig.strategy_name, 1.0)
            if weight == 0.0:
                continue
            weighted_strength = sig.score * weight * sig.confidence

            if sig.signal == SignalType.BUY:
                buy_score += weighted_strength
                buy_strategies.append(sig.strategy_name)
                reasons.append(sig.reason)
            elif sig.signal == SignalType.SELL:
                sell_score += weighted_strength
                sell_strategies.append(sig.strategy_name)
                reasons.append(sig.reason)

        # ML 신호 추가
        if ml_signal and ml_confidence > 0.45:
            ml_weight = self.STRATEGY_WEIGHTS[StrategyKey.ML_ENSEMBLE]
            if ml_signal == "BUY":
                buy_score += ml_weight * ml_confidence
                buy_strategies.append(StrategyKey.ML_ENSEMBLE)
            elif ml_signal == "SELL":
                sell_score += ml_weight * ml_confidence
                sell_strategies.append(StrategyKey.ML_ENSEMBLE)
            elif ml_signal == "HOLD" and len(buy_strategies) >= 4 and ml_confidence >= 0.50:
                # [R2-PATCH] 조건 강화(≥3→≥4, conf≥0.5) + 가짜 이름 제거
                buy_score += ml_weight * ml_confidence * 0.3
                buy_strategies.append(StrategyKey.ML_ENSEMBLE)

        total_strategies = len(filtered_signals) + (1 if ml_signal else 0)
        net_score        = buy_score - sell_score

        if net_score >= self.buy_threshold:
            n_buy          = len(buy_strategies)
            agreement_rate = n_buy / max(total_strategies, 1)
            if agreement_rate < self.min_agreement:
                return None

            # ── BUG-5 FIX: v1 신호 없고 ML만 있을 때 avg_confidence=0 방지 ──
            _v1_buy_sigs = [s for s in filtered_signals if s.signal == SignalType.BUY]
            if _v1_buy_sigs:
                avg_confidence = sum(s.confidence for s in _v1_buy_sigs) / len(_v1_buy_sigs)
            elif ml_confidence > 0.0:
                avg_confidence = ml_confidence
            else:
                avg_confidence = 0.0
            # ── BUG-5 FIX 끝 ──────────────────────────────────────────────
            return CombinedSignal(
                market=market,
                signal_type=SignalType.BUY,
                score=buy_score,
                confidence=avg_confidence,
                agreement_rate=agreement_rate,
                contributing_strategies=buy_strategies,
                reasons=reasons[:5],
                metadata={"buy_score": buy_score, "sell_score": sell_score, "regime": regime},
                ml_signal=ml_signal,
                ml_confidence=ml_confidence,
            )

        elif net_score <= self.sell_threshold:
            n_sell         = len(sell_strategies)
            agreement_rate = n_sell / max(total_strategies, 1)
            if agreement_rate < self.min_agreement and not (
                ml_signal == "SELL" and ml_confidence > 0.52
            ):
                return None

            # [U8-PATCH] SELL confidence fallback — BUY 로직과 대칭화
            _v1_sell_sigs = [s for s in filtered_signals if s.signal == SignalType.SELL]
            if _v1_sell_sigs:
                avg_confidence = sum(s.confidence for s in _v1_sell_sigs) / len(_v1_sell_sigs)
            elif ml_confidence > 0.0:
                avg_confidence = ml_confidence
            else:
                avg_confidence = 0.0
            return CombinedSignal(
                market=market,
                signal_type=SignalType.SELL,
                score=-sell_score,
                confidence=avg_confidence,
                agreement_rate=agreement_rate,
                contributing_strategies=sell_strategies,
                reasons=reasons[:5],
                metadata={"buy_score": buy_score, "sell_score": sell_score, "regime": regime},
                ml_signal=ml_signal,
                ml_confidence=ml_confidence,
            )

        return None  # HOLD

    def _filter_by_regime(self, signals: List[Signal], regime: str) -> List[Signal]:
        # [FP7-PATCH] 비활성 전략 사전 필터링 (부스트 연산 불필요 방지)
        signals = [s for s in signals if s.strategy_name not in DISABLED_STRATEGIES
                   and self.STRATEGY_WEIGHTS.get(s.strategy_name, 1.0) > 0.0]
        preferred = self.REGIME_PREFERRED.get(regime.upper(), None)
        if preferred is None:
            return signals
        return [
            self._boost_signal(sig, score_mult=1.2, reason_suffix="[레짐부스트]")
            if sig.strategy_name in preferred else sig
            for sig in signals
        ]

    @staticmethod
    def _boost_signal(sig: Signal, score_mult: float = 1.0, reason_suffix: str = "") -> Signal:
        from strategies.base_strategy import StrategySignal
        return StrategySignal(
            strategy_name=sig.strategy_name,
            market=sig.market,
            signal=sig.signal,
            score=min(sig.score * score_mult, 1.0),
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
        return {
            sig.strategy_name: {
                "type":           sig.signal.name,
                "strength":       sig.score,
                "confidence":     sig.confidence,
                "weight":         self.STRATEGY_WEIGHTS.get(sig.strategy_name, 1.0),
                "weighted_score": sig.score * self.STRATEGY_WEIGHTS.get(sig.strategy_name, 1.0) * sig.confidence,
            }
            for sig in signals
        }
