from __future__ import annotations
import time
import sqlite3
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from loguru import logger
from strategies.base_strategy import BaseStrategy, Signal, SignalType
from strategies.v2.context.market_context import MarketContextEngine, MarketContext
from strategies.v2.order_block_v2 import OrderBlockStrategy2
from strategies.v2.vol_breakout_v2 import VolBreakoutStrategy2
from strategies.v2.supertrend_v2 import SupertrendStrategy2
from strategies.v2.vwap_v2 import VWAPReversionStrategy2
from strategies.v2.macd_v2 import MACDCrossStrategy2
from strategies.v2.rsi_v2 import RSIDivergenceStrategy2
from strategies.v2.bollinger_v2 import BollingerSqueezeStrategy2
from strategies.v2.atr_v2 import ATRChannelStrategy2


@dataclass
class StrategyWeight:
    name: str
    base_weight: float
    recent_wr: float        # 최근 20거래 승률
    dynamic_weight: float   # 최종 동적 가중치
    signal_count: int = 0   # 오늘 신호 수
    win_count: int    = 0   # 오늘 승리 수


@dataclass
class EnsembleDecision:
    should_enter: bool
    final_score: float
    confidence: float
    position_size_mult: float   # 0.5 ~ 1.5
    signals_fired: List[str]
    dominant_strategy: str
    regime: str
    reasoning: str


class EnsembleEngine:
    """
    앙상블 최종 결정 엔진
    8개 전략의 동적 가중치 합산
    최근 20거래 승률로 가중치 자동 조정
    시장 레짐별 전략 우선순위 변경
    """

    # 기본 가중치 (config/optimized_params.json 우선, 없으면 아래 기본값)
    @staticmethod
    def _load_base_weights() -> dict:
        try:
            import sys, pathlib as _pl
            sys.path.insert(0, str(_pl.Path(__file__).parent.parent.parent))
            from config.strategy_config_loader import get_ensemble_weights
            w = get_ensemble_weights()
            # config 키(Order_Block 등) → 내부 키(OrderBlock_SMC 등) 매핑
            _map = {
                "Order_Block":       "OrderBlock_SMC",
                "Bollinger_Squeeze": "Bollinger_Squeeze",
                "RSI_Divergence":    "RSI_Divergence",
                "MACD_Cross":        "MACD_Cross",
                "ATR_Channel":       "ATR_Channel",
                "VWAP_Reversion":    "VWAP_Reversion",
                "Supertrend":        "Supertrend",
                "Vol_Breakout":      "VolBreakout",
            }
            mapped = {}
            for cfg_k, eng_k in _map.items():
                if cfg_k in w:
                    mapped[eng_k] = w[cfg_k]
            if mapped:
                logger.info(f"[Ensemble] config 가중치 {len(mapped)}개 로드 완료")
                return mapped
        except Exception as e:
            logger.warning(f"[Ensemble] config 가중치 로드 실패(기본값 사용): {e}")
        return {}

    BASE_WEIGHTS = {
        "MACD_Cross":       1.0,
        "RSI_Divergence":   1.5,
        "Bollinger_Squeeze":1.0,
        "ATR_Channel":      1.5,
        "OrderBlock_SMC":   1.2,
        "VolBreakout":      1.0,
        "Supertrend":       0.8,
        "VWAP_Reversion":   0.9,
    }

    # 레짐별 전략 우선순위 보정
    REGIME_BOOSTS = {
        "TRENDING_UP": {
            "MACD_Cross": 0.3,
            "Supertrend": 0.4,
            "VolBreakout": 0.2,
        },
        "TRENDING_DOWN": {
            "ATR_Channel": 0.2,
        },
        "RANGING": {
            "VWAP_Reversion":   0.4,
            "Bollinger_Squeeze":0.3,
            "RSI_Divergence":   0.2,
        },
        "VOLATILE": {
            "ATR_Channel":      0.3,
            "OrderBlock_SMC":   0.2,
            "Bollinger_Squeeze":0.2,
        },
    }

    # 진입 임계값
    ENTRY_THRESHOLD     = 0.55   # 이 점수 이상이면 진입
    MIN_SIGNALS_NEEDED  = 2      # 최소 신호 개수
    REFERENCE_WR        = 0.60   # 동적 가중치 기준 승률

    def __init__(self, db_path: str = "database/apex_bot.db"):
        self._db_path        = db_path
        self._context_engine = MarketContextEngine()
        # config/optimized_params.json 가중치 적용
        try:
            import sys as _sys, pathlib as _pl
            _sys.path.insert(0, str(_pl.Path(__file__).parent.parent.parent))
            _cfg_w = get_ensemble_weights()
            _KEY_MAP = {
                "Order_Block":       "OrderBlock_SMC",
                "Bollinger_Squeeze": "Bollinger_Squeeze",
                "RSI_Divergence":    "RSI_Divergence",
                "MACD_Cross":        "MACD_Cross",
                "ATR_Channel":       "ATR_Channel",
                "VWAP_Reversion":    "VWAP_Reversion",
                "Supertrend":        "Supertrend",
                "Vol_Breakout":      "VolBreakout",
            }
            for cfg_k, eng_k in _KEY_MAP.items():
                if cfg_k in _cfg_w and eng_k in self.BASE_WEIGHTS:
                    self.BASE_WEIGHTS[eng_k] = _cfg_w[cfg_k]
            logger.info(f'[Ensemble] config 가중치 {len(_cfg_w)}개 적용 완료')
        except Exception as _cw_e:
            logger.warning(f'[Ensemble] config 가중치 로드 실패(기본값): {_cw_e}')
        self._weights: Dict[str, StrategyWeight] = {}
        self._strategies: Dict[str, BaseStrategy] = {}
        self._init_strategies()
        self._load_recent_performance()

    def _init_strategies(self):
        self._strategies = {
            "MACD_Cross":        MACDCrossStrategy2(),
            "RSI_Divergence":    RSIDivergenceStrategy2(),
            "Bollinger_Squeeze": BollingerSqueezeStrategy2(),
            "ATR_Channel":       ATRChannelStrategy2(),
            "OrderBlock_SMC":    OrderBlockStrategy2(),
            "VolBreakout":       VolBreakoutStrategy2(),
            "Supertrend":        SupertrendStrategy2(),
            "VWAP_Reversion":    VWAPReversionStrategy2(),
        }
        for name, base_w in self.BASE_WEIGHTS.items():
            self._weights[name] = StrategyWeight(
                name=name,
                base_weight=base_w,
                recent_wr=self.REFERENCE_WR,
                dynamic_weight=base_w,
            )
        logger.info(f"[Ensemble] 8개 전략 초기화 완료")

    def _load_recent_performance(self):
        """DB에서 최근 20거래 승률 로드 → 동적 가중치 계산"""
        try:
            conn = sqlite3.connect(self._db_path)
            for name in self.BASE_WEIGHTS:
                rows = conn.execute(
                    """
                    SELECT profit_rate FROM trade_history
                    WHERE strategy = ? AND side = 'SELL'
                    ORDER BY timestamp DESC LIMIT 20
                    """,
                    (name,),
                ).fetchall()

                if len(rows) >= 5:
                    wins    = sum(1 for r in rows if r[0] > 0)
                    wr      = wins / len(rows)
                    perf_mult = wr / self.REFERENCE_WR
                    new_w     = self._weights[name].base_weight * perf_mult
                    self._weights[name].recent_wr      = wr
                    self._weights[name].dynamic_weight = round(new_w, 3)
                    logger.info(
                        f"[Ensemble] {name:20s} WR={wr:.1%} "
                        f"→ weight={new_w:.2f}"
                    )
            conn.close()
        except Exception as e:
            logger.warning(f"[Ensemble] 성과 로드 실패: {e}")

    def decide(
        self,
        df: pd.DataFrame,
        market: str,
        ctx: Optional[MarketContext] = None,
    ) -> EnsembleDecision:
        """메인 진입 결정 함수"""
        try:
            if ctx is None:
                ctx = self._context_engine.analyze(df, market)

            signals: Dict[str, Signal] = {}

            # 각 전략 신호 수집
            for name, strategy in self._strategies.items():
                try:
                    sig = strategy.generate_signal(df, market)
                    if sig is not None and sig.signal == SignalType.BUY:
                        signals[name] = sig
                except Exception as e:
                    logger.warning(f"[Ensemble] {name} 신호 오류: {e}")

            if len(signals) < self.MIN_SIGNALS_NEEDED:
                return EnsembleDecision(
                    should_enter=False,
                    final_score=0.0,
                    confidence=0.0,
                    position_size_mult=1.0,
                    signals_fired=[],
                    dominant_strategy="",
                    regime=ctx.regime,
                    reasoning=f"신호 부족 ({len(signals)}/{self.MIN_SIGNALS_NEEDED})",
                )

            # 동적 가중치 합산
            regime_boosts = self.REGIME_BOOSTS.get(ctx.regime, {})
            total_score   = 0.0
            total_weight  = 0.0
            best_name     = ""
            best_score    = 0.0

            for name, sig in signals.items():
                w     = self._weights[name].dynamic_weight
                boost = regime_boosts.get(name, 0.0)
                final_w = w + boost
                score   = sig.confidence * final_w
                total_score  += score
                total_weight += final_w
                if score > best_score:
                    best_score = score
                    best_name  = name

            normalized = total_score / total_weight if total_weight > 0 else 0.0

            # 포지션 사이즈 결정
            if normalized >= 0.75:
                size_mult = 1.5
            elif normalized >= 0.65:
                size_mult = 1.2
            elif normalized >= 0.55:
                size_mult = 1.0
            else:
                size_mult = 0.5

            should_enter = normalized >= self.ENTRY_THRESHOLD

            reasoning = (
                f"레짐={ctx.regime} | "
                f"신호={len(signals)}개 | "
                f"점수={normalized:.3f} | "
                f"주도전략={best_name}"
            )

            if should_enter:
                logger.info(
                    f"[Ensemble] ✅ {market} 진입결정 | {reasoning} | "
                    f"사이즈배수={size_mult:.1f}"
                )
            else:
                logger.debug(
                    f"[Ensemble] ❌ {market} 진입거부 | {reasoning}"
                )

            return EnsembleDecision(
                should_enter=should_enter,
                final_score=normalized,
                confidence=normalized,
                position_size_mult=size_mult,
                signals_fired=list(signals.keys()),
                dominant_strategy=best_name,
                regime=ctx.regime,
                reasoning=reasoning,
            )

        except Exception as e:
            logger.warning(f"[Ensemble] {market} 결정 오류: {e}")
            return EnsembleDecision(
                should_enter=False,
                final_score=0.0,
                confidence=0.0,
                position_size_mult=1.0,
                signals_fired=[],
                dominant_strategy="",
                regime="UNKNOWN",
                reasoning=f"오류: {e}",
            )

    def update_result(self, strategy_name: str, profit_rate: float):
        """거래 결과 반영 → 동적 가중치 실시간 업데이트"""
        if strategy_name not in self._weights:
            return
        w = self._weights[strategy_name]
        w.signal_count += 1
        if profit_rate > 0:
            w.win_count += 1
        if w.signal_count >= 5:
            new_wr    = w.win_count / w.signal_count
            perf_mult = new_wr / self.REFERENCE_WR
            new_w     = w.base_weight * perf_mult
            w.recent_wr      = new_wr
            w.dynamic_weight = round(max(0.1, min(new_w, 3.0)), 3)
            logger.info(
                f"[Ensemble] 가중치 업데이트 | {strategy_name} | "
                f"WR={new_wr:.1%} → weight={new_w:.2f}"
            )

    def get_weight_summary(self) -> str:
        lines = ["=== Ensemble 가중치 현황 ==="]
        for name, w in self._weights.items():
            lines.append(
                f"{name:20s} base={w.base_weight:.1f} "
                f"WR={w.recent_wr:.1%} "
                f"dynamic={w.dynamic_weight:.2f}"
            )
        return "\n".join(lines)