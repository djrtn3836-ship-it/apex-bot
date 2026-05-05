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
            from datetime import datetime as _dt_boost  # [FX-3] 누락 import 추가
            import pytz as _pytz_boost                  # [FX-3] 누락 import 추가
            hour = _dt_boost.now(_pytz_boost.timezone("Asia/Seoul")).hour
            return get_boost(strategy_name, hour)
        except Exception as _e:
            return 1.0  # fallback: boost 미적용

    def check(
        self,
        df: "pd.DataFrame",
        market: str,
        v1_confidence: float = 0.0,
        fallback_regime: str = "RANGING",  # [EN-M3] engine_buy에서 GlobalRegime 주입
    ) -> "tuple[bool, float, float]":
        """
        v1 신호와 v2 앙상블 신호를 결합해 최종 진입 여부 반환
        Returns:
            (should_enter, final_confidence, size_multiplier)
        """
        # [EN-M3-i] loguru logger 사용 (import logging 제거)
        from loguru import logger as _logger

        # ── 시간필터: config 기반 전략별 허용 시간대 체크 ──
        try:
            # [FX-4] self._strategies → self._ensemble._strategies (올바른 참조)
            _strat_names = list(getattr(self._ensemble, '_strategies', {}).keys())
            blocked = [n for n in _strat_names if not self._time_filter_pass(n)]
            if blocked:
                _logger.debug(f"[V2Layer] 시간필터 차단 전략: {blocked}")
        except Exception:
            blocked = []

        if not self._enabled:
            return True, v1_confidence, 1.0

        try:
            decision = self._ensemble.decide(df, market, fallback_regime=fallback_regime)
            if decision is None:
                return True, v1_confidence, 1.0

            boost = self._get_boost(decision.dominant_strategy or "")

            # ── BUG-1 FIX: v2 신호 없을 때 신뢰도 0.4배 축소 방지 ──
            _v2_has_signal = decision.confidence >= 0.10 and len(decision.signals_fired) > 0
            if _v2_has_signal:
                combined_conf = v1_confidence * 0.4 + decision.confidence * 0.6
            else:
                combined_conf = v1_confidence  # v2 신호 없음 → v1 보존
            # ── BUG-1 FIX 끝 ────────────────────────────────────────
            size_mult = decision.position_size_mult * boost

            # [FX-5] 0.45 하드코딩 → settings.buy_signal_threshold 연동
            try:
                from config.settings import get_settings as _gs
                _v2_conf_thr = getattr(_gs().trading, 'buy_signal_threshold', 0.45)
            except Exception:
                _v2_conf_thr = 0.45
            if decision.should_enter and combined_conf >= _v2_conf_thr:
                _logger.info(
                    f"[V2Layer] {market} v1+v2 합의 "
                    f"v1={v1_confidence:.2f} v2={decision.confidence:.2f} "
                    f"combined={combined_conf:.2f} size={size_mult:.2f} boost={boost:.2f}"
                )
                return True, combined_conf, size_mult
            elif not decision.should_enter and decision.confidence >= 0.65:
                _logger.info(f"[V2Layer] {market} v2 거부 conf={decision.confidence:.2f}")
                return False, combined_conf, 1.0
            elif not decision.should_enter and decision.confidence >= _v2_conf_thr:
                # [BUG-E FIX] v2가 신뢰도 0.45 이상으로 거부 → v1도 차단
                _logger.info(
                    f"[V2Layer] {market} v2 신뢰거부 conf={decision.confidence:.2f} "
                    f"(thr={_v2_conf_thr:.2f}) → 진입 차단"
                )
                return False, combined_conf, 1.0
            else:
                # v2 신호 부족(신뢰도 낮음) → v1 신호 통과 허용 (데이터 부족 시 폴백)
                _logger.debug(
                    f"[V2Layer] {market} v2 미결정 conf={decision.confidence:.2f} → v1 폴백"
                )
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