"""
core/market_regime.py
─────────────────────────────────────────────────────────────
글로벌 마켓 레짐 감지기 v1.0.0

역할:
    - BTC EMA200 기반 전체 시장 상태 판단 (BULL/BEAR/SIDEWAYS/RECOVERY)
    - 레짐별 매수 허용/차단/조건부 허용 결정
    - 업비트 현물 전용: 하락장 현금 보유, 급등 코인만 조건부 진입

레짐 정의:
    BULL      : BTC > EMA200 + 2%   → 모든 매수 허용
    RECOVERY  : EMA200 - 2% < BTC ≤ EMA200 + 2%   → 조건부 허용
    BEAR_WATCH: EMA200 - 5% < BTC ≤ EMA200 - 2%   → 급등 코인만 허용
    BEAR      : BTC ≤ EMA200 - 5%   → 일반 매수 차단, 급등 코인만 허용
─────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import time
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Tuple
import numpy as np
import pandas as pd
from loguru import logger


class GlobalRegime(Enum):
    BULL       = "BULL"        # 강세장 - 모든 매수 허용
    RECOVERY   = "RECOVERY"    # 회복장 - 조건부 허용
    BEAR_WATCH = "BEAR_WATCH"  # 약세 경계 - 급등 코인만
    BEAR       = "BEAR"        # 약세장 - 일반 차단, 급등만
    UNKNOWN    = "UNKNOWN"     # 데이터 부족


# 레짐별 매수 정책
REGIME_POLICY: Dict[str, Dict] = {
    "BULL": {
        "allow_normal_buy":  True,
        "allow_surge_buy":   True,
        "position_size_pct": 1.0,    # 기본 포지션 100%
        "surge_size_pct":    1.0,
        "min_ml_score":      0.62,
        "description":       "강세장 - 전략 모두 허용",
    },
    "RECOVERY": {
        "allow_normal_buy":  True,
        "allow_surge_buy":   True,
        "position_size_pct": 0.8,    # 포지션 80%로 축소
        "surge_size_pct":    1.0,
        "min_ml_score":      0.64,
        "description":       "회복장 - 보수적 진입",
    },
    "BEAR_WATCH": {
        "allow_normal_buy":  False,  # 일반 매수 차단
        "allow_surge_buy":   True,   # 급등만 허용
        "position_size_pct": 0.0,
        "surge_size_pct":    0.5,    # 급등 진입 50%
        "min_ml_score":      0.68,
        "description":       "약세 경계 - 급등 코인만",
    },
    "BEAR": {
        "allow_normal_buy":  False,
        "allow_surge_buy":   True,
        "position_size_pct": 0.0,
        "surge_size_pct":    0.4,    # 급등 진입 40%
        "min_ml_score":      0.70,
        "description":       "약세장 - 급등 코인만 (엄격)",
    },
    "UNKNOWN": {
        "allow_normal_buy":  False,
        "allow_surge_buy":   False,
        "position_size_pct": 0.0,
        "surge_size_pct":    0.0,
        "min_ml_score":      1.0,
        "description":       "데이터 부족 - 전체 차단",
    },
}


class GlobalMarketRegimeDetector:
    """BTC EMA200 기반 글로벌 마켓 레짐 감지기"""

    # BTC EMA200 기준 임계값
    BULL_THRESHOLD       =  0.02   # +2% 이상 → BULL
    RECOVERY_THRESHOLD   =  0.00   # 0% ~ +2% → RECOVERY (EMA200 위)
    BEAR_WATCH_THRESHOLD = -0.05   # -5% ~ 0% → BEAR_WATCH
    # -5% 미만 → BEAR

    # 캐시 TTL (초)
    CACHE_TTL = 300  # 5분

    def __init__(self):
        self._cache: Optional[GlobalRegime] = None
        self._cache_ts: float = 0.0
        self._last_btc_price: float = 0.0
        self._last_ema200: float = 0.0
        self._last_deviation: float = 0.0
        self._regime_history: list = []   # 최근 10개 레짐 기록
        logger.info("GlobalMarketRegimeDetector 초기화 완료")

    def detect(self, btc_df: pd.DataFrame) -> GlobalRegime:
        """
        BTC 일봉/1시간봉 기준 글로벌 레짐 감지

        Args:
            btc_df: BTC OHLCV DataFrame (close 컬럼 필수, 최소 210행)

        Returns:
            GlobalRegime enum
        """
        # 캐시 확인
        if self._cache is not None and (time.time() - self._cache_ts) < self.CACHE_TTL:
            return self._cache

        if btc_df is None or len(btc_df) < 50:
            logger.warning("GlobalRegime: BTC 데이터 부족 → UNKNOWN")
            return GlobalRegime.UNKNOWN

        try:
            close = btc_df["close"].astype(float)
            price = float(close.iloc[-1])

            # EMA200 계산 (데이터가 200개 미만이면 가용한 최대로)
            ema_period = min(200, len(close) - 1)
            ema200 = float(close.ewm(span=ema_period, adjust=False).mean().iloc[-1])

            deviation = (price - ema200) / ema200  # EMA200 대비 이격도

            # 레짐 판단
            if deviation >= self.BULL_THRESHOLD:
                regime = GlobalRegime.BULL
            elif deviation >= self.RECOVERY_THRESHOLD:
                regime = GlobalRegime.RECOVERY
            elif deviation >= self.BEAR_WATCH_THRESHOLD:
                regime = GlobalRegime.BEAR_WATCH
            else:
                regime = GlobalRegime.BEAR

            # 스무딩: 직전 레짐과 비교해 급격한 변동 완화
            regime = self._smooth_regime(regime)

            # 캐시 업데이트
            self._cache = regime
            self._cache_ts = time.time()
            self._last_btc_price = price
            self._last_ema200 = ema200
            self._last_deviation = deviation

            # 히스토리 기록
            self._regime_history.append({
                "time":      datetime.now().strftime("%H:%M:%S"),
                "regime":    regime.value,
                "btc_price": price,
                "ema200":    round(ema200, 0),
                "deviation": round(deviation * 100, 2),
            })
            if len(self._regime_history) > 10:
                self._regime_history.pop(0)

            policy = REGIME_POLICY[regime.value]
            logger.info(
                f"[GlobalRegime] {regime.value} | "
                f"BTC={price:,.0f} EMA200={ema200:,.0f} "
                f"이격={deviation*100:+.2f}% | {policy['description']}"
            )
            return regime

        except Exception as e:
            logger.error(f"GlobalRegime 감지 오류: {e}")
            return GlobalRegime.UNKNOWN

    def _smooth_regime(self, new_regime: GlobalRegime) -> GlobalRegime:
        """
        레짐 급변동 완화 (노이즈 필터)
        직전 3개 레짐이 모두 다른 방향이면 기존 유지
        """
        if len(self._regime_history) < 2:
            return new_regime

        recent = [h["regime"] for h in self._regime_history[-2:]]
        # 직전 2개가 모두 현재와 다르면 → 기존 캐시 유지
        if self._cache and all(r != new_regime.value for r in recent):
            # 단, 레짐 계층 차이가 크면 (BULL→BEAR) 즉시 반영
            regime_order = ["BULL", "RECOVERY", "BEAR_WATCH", "BEAR", "UNKNOWN"]
            old_idx = regime_order.index(self._cache.value) if self._cache.value in regime_order else 2
            new_idx = regime_order.index(new_regime.value) if new_regime.value in regime_order else 2
            if abs(new_idx - old_idx) >= 2:
                return new_regime  # 급격한 변동은 즉시 반영
            return self._cache

        return new_regime

    def get_policy(self, regime: Optional[GlobalRegime] = None) -> Dict:
        """현재 레짐의 매수 정책 반환"""
        if regime is None:
            regime = self._cache or GlobalRegime.UNKNOWN
        return REGIME_POLICY.get(regime.value, REGIME_POLICY["UNKNOWN"])

    def allow_normal_buy(self, regime: Optional[GlobalRegime] = None) -> bool:
        """일반 매수 허용 여부"""
        return self.get_policy(regime)["allow_normal_buy"]

    def allow_surge_buy(self, regime: Optional[GlobalRegime] = None) -> bool:
        """급등 매수 허용 여부"""
        return self.get_policy(regime)["allow_surge_buy"]

    def get_position_size_ratio(self, regime: Optional[GlobalRegime] = None,
                                 is_surge: bool = False) -> float:
        """레짐별 포지션 크기 비율"""
        policy = self.get_policy(regime)
        return policy["surge_size_pct"] if is_surge else policy["position_size_pct"]

    def get_min_ml_score(self, regime: Optional[GlobalRegime] = None) -> float:
        """레짐별 최소 ML 점수 임계값"""
        return self.get_policy(regime)["min_ml_score"]

    def get_status(self) -> Dict:
        """현재 레짐 상태 요약"""
        regime = self._cache or GlobalRegime.UNKNOWN
        policy = self.get_policy(regime)
        return {
            "regime":          regime.value,
            "btc_price":       self._last_btc_price,
            "ema200":          self._last_ema200,
            "deviation_pct":   round(self._last_deviation * 100, 2),
            "allow_normal":    policy["allow_normal_buy"],
            "allow_surge":     policy["allow_surge_buy"],
            "position_ratio":  policy["position_size_pct"],
            "surge_ratio":     policy["surge_size_pct"],
            "min_ml_score":    policy["min_ml_score"],
            "description":     policy["description"],
            "history":         self._regime_history[-3:],
        }

    def force_refresh(self):
        """캐시 강제 초기화 (즉시 재계산)"""
        self._cache = None
        self._cache_ts = 0.0
