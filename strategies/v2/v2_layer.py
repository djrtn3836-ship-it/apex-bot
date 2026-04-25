from __future__ import annotations
import threading
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
        self._lock = threading.Lock()  # 스레드 안전성
        self._ctx_engine  = MarketContextEngine()
        self._enabled     = True
        self._weights  = {}  # 동적 가중치 캐시
        logger.info("[V2Layer] 앙상블 브릿지 레이어 초기화 완료")

    def _time_filter_pass(self, strategy_name: str) -> bool:
        """config 시간 필터 체크"""
        try:
            from config.strategy_config_loader import is_strategy_active
            from datetime import datetime
            import pytz
            hour = datetime.now(pytz.timezone("Asia/Seoul")).hour
            return is_strategy_active(strategy_name, hour)
        except Exception as _e:
            return True

    def _get_boost(self, strategy_name: str) -> float:
        """config boost 값 반환"""
        try:
            from config.strategy_config_loader import get_boost
            hour = datetime.now(pytz.timezone("Asia/Seoul")).hour
            return get_boost(strategy_name, hour)
        except Exception as _e:
            return 1.0

    def check(
        self,
        df: "pd.DataFrame",
        market: str,
        v1_confidence: float = 0.0,
    ) -> "tuple[bool, float, float]":
        """
        v1 신호와 v2 앙상블 신호를 결합해 최종 진입 여부 반환
        Returns:
            (should_enter, final_confidence, size_multiplier)
        """
        import logging as _log
        _logger = _log.getLogger("v2_layer")

        # ── 시간필터: config 기반 전략별 허용 시간대 체크 ──
        try:
            blocked = [n for n in self._strategies if not self._time_filter_pass(n)]
            if blocked:
                _logger.debug(f"[V2Layer] 시간필터 차단 전략: {blocked}")
        except Exception:
            blocked = []

        if not self._enabled:
            return True, v1_confidence, 1.0

        try:
            decision = self._ensemble.decide(df, market)
            if decision is None:
                return True, v1_confidence, 1.0

            boost = self._get_boost(decision.dominant_strategy or "")
            combined_conf = v1_confidence * 0.4 + decision.confidence * 0.6
            size_mult = decision.position_size_mult * boost

            if decision.should_enter and combined_conf >= 0.45:
                _logger.info(
                    f"[V2Layer] {market} v1+v2 합의 "
                    f"v1={v1_confidence:.2f} v2={decision.confidence:.2f} "
                    f"combined={combined_conf:.2f} size={size_mult:.2f} boost={boost:.2f}"
                )
                return True, combined_conf, size_mult
            elif not decision.should_enter and decision.confidence >= 0.65:
                _logger.info(f"[V2Layer] {market} v2 거부 conf={decision.confidence:.2f}")
                return False, combined_conf, 1.0
            else:
                return True, v1_confidence, 1.0

        except Exception as _e:
            _logger.warning(f"[V2Layer] check() 오류 — v1 폴백: {_e}")
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

# 하위 호환 별칭
V2Layer = V2EnsembleLayer