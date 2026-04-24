from __future__ import annotations
import asyncio
from typing import Optional
import pandas as pd
from loguru import logger
from strategies.v2.ensemble_engine import EnsembleEngine
from strategies.v2.context.market_context import MarketContextEngine


class V2EnsembleLayer:
    """
    v1 전략과 v2 앙상블을 병렬 운영하는 브릿지 레이어
    v1 신호 AND v2 신호 동시 충족 시에만 진입 허용
    v2 점수로 포지션 사이즈 보정
    """

    def __init__(self, db_path: str = "database/apex_bot.db"):
        self._ensemble    = EnsembleEngine(db_path)
        self._ctx_engine  = MarketContextEngine()
        self._enabled     = True
        logger.info("[V2Layer] 앙상블 브릿지 레이어 초기화 완료")

    def _time_filter_pass(self, strategy_name: str) -> bool:
        """config 시간 필터 체크"""
        try:
            from config.strategy_config_loader import is_strategy_active
            from datetime import datetime
            import pytz
            hour = datetime.now(pytz.timezone("Asia/Seoul")).hour
            return is_strategy_active(strategy_name, hour)
        except Exception:
            return True

    def _get_boost(self, strategy_name: str) -> float:
        """config boost 값 반환"""
        try:
            from config.strategy_config_loader import get_boost
            from datetime import datetime
            import pytz
            hour = datetime.now(pytz.timezone("Asia/Seoul")).hour
            return get_boost(strategy_name, hour)
        except Exception:
            return 1.0

    def check(
        self,
        df: pd.DataFrame,
        market: str,
        v1_confidence: float,
    ) -> tuple[bool, float, float]:
        """
        v1 신호와 v2 앙상블 신호를 결합해 최종 진입 여부 반환
        Returns:
            (should_enter, final_confidence, size_multiplier)
        """
        if not self._enabled:
            return True, v1_confidence, 1.0

        try:
            ctx      = self._ctx_engine.analyze(df, market)
            decision = self._ensemble.decide(df, market, ctx)

            # v2 신호 없으면 v1만으로 진입 (보수적 허용)
            if not decision.signals_fired:
                logger.debug(
                    f"[V2Layer] {market} v2 신호 없음 → v1 단독 진입 허용"
                )
                return True, v1_confidence, 1.0

            # v2 진입 거부 시 차단
            if not decision.should_enter:
                logger.info(
                    f"[V2Layer] ❌ {market} v2 앙상블 거부 | "
                    f"점수={decision.final_score:.3f} | "
                    f"이유={decision.reasoning}"
                )
                return False, 0.0, 0.0

            # v1 + v2 모두 허용 → 신뢰도 합산
            combined_conf = (v1_confidence * 0.4 + decision.confidence * 0.6)
            size_mult     = decision.position_size_mult

            logger.info(
                f"[V2Layer] ✅ {market} v1+v2 합의 | "
                f"v1={v1_confidence:.2f} v2={decision.confidence:.2f} "
                f"→ 합산={combined_conf:.2f} | "
                f"사이즈={size_mult:.1f}x | "
                f"발화={decision.signals_fired}"
            )

            return True, combined_conf, size_mult

        except Exception as e:
            logger.warning(f"[V2Layer] {market} 오류 → v1 단독 허용: {e}")
            return True, v1_confidence, 1.0

    def update_result(self, strategy_name: str, profit_rate: float):
        """거래 결과를 앙상블 엔진에 반영"""
        self._ensemble.update_result(strategy_name, profit_rate)

    def enable(self):
        self._enabled = True
        logger.info("[V2Layer] 활성화")

    def disable(self):
        self._enabled = False
        logger.info("[V2Layer] 비활성화 (v1 단독 운영)")

    def get_status(self) -> str:
        return self._ensemble.get_weight_summary()
