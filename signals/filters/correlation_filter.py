"""
APEX BOT - 상관관계 필터 (Correlation Filter)
BTC 급락 시 알트코인 동시 손절 방지
- BTC 가격 변화율이 임계치 초과 시 알트코인 신규 매수 차단
- 상관관계 높은 코인 동시 포지션 제한
- 시장 충격(Market Shock) 감지
"""
from __future__ import annotations

import time
from collections import deque
from typing import Dict, List, Optional, Tuple
import numpy as np
from loguru import logger


class CorrelationFilter:
    """
    BTC 기반 상관관계 리스크 필터

    주요 기능:
    1. BTC 단기 급락 감지 → 알트 매수 차단
    2. 포트폴리오 상관관계 계산 → 동시 포지션 제한
    3. 시장 충격 감지 (Market Shock Detection)
    4. 변동성 스파이크 감지

    사용법:
        cf = CorrelationFilter()
        cf.update_price("KRW-BTC", btc_price)

        ok, reason = cf.can_buy("KRW-ETH", open_positions)
        if not ok:
            logger.warning(f"상관관계 필터 차단: {reason}")
    """

    BTC_MARKET = "KRW-BTC"

    # 급락 감지 임계값
    BTC_SHOCK_5MIN  = -0.025   # 5분 내 -2.5% → 알트 매수 차단
    BTC_SHOCK_1H    = -0.050   # 1시간 내 -5.0% → 강력 차단
    BTC_SPIKE_5MIN  = +0.030   # 5분 내 +3.0% → 매도 주의

    # 변동성 임계값 (ATR 대비 배수)
    VOL_SPIKE_MULT  = 2.5      # 평균 변동성 × 2.5 초과 시 차단

    # 차단 지속 시간
    BLOCK_DURATION_SHOCK = 30 * 60    # 급락 감지 후 30분 차단
    BLOCK_DURATION_SEVERE = 120 * 60  # 심각 급락 후 2시간 차단

    def __init__(self,
                 btc_shock_threshold: float = BTC_SHOCK_5MIN,
                 block_duration: int = BLOCK_DURATION_SHOCK):
        self._btc_shock_threshold = btc_shock_threshold
        self._block_duration = block_duration

        # 가격 이력 (market → deque of (timestamp, price))
        self._price_history: Dict[str, deque] = {}

        # 차단 상태 (market → unblock_time)
        self._blocked_until: Dict[str, float] = {}
        self._global_block_until: float = 0.0  # 전체 차단
        self._block_reason: str = ""

        # 변동성 추적
        self._volatility: Dict[str, deque] = {}

        logger.info("✅ 상관관계 필터 초기화")

    # ── Price Feed ──────────────────────────────────────────────────

    def update_price(self, market: str, price: float):
        """가격 업데이트 (엔진에서 매 tick 호출)"""
        if market not in self._price_history:
            self._price_history[market] = deque(maxlen=360)  # 최대 6시간(1분봉)

        self._price_history[market].append((time.time(), price))
        self._volatility.setdefault(market, deque(maxlen=60))

        # BTC 가격이면 충격 감지 실행
        if market == self.BTC_MARKET:
            self._check_btc_shock()

    def update_prices(self, price_map: Dict[str, float]):
        """다중 마켓 가격 일괄 업데이트"""
        for market, price in price_map.items():
            self.update_price(market, price)

    # ── Core Filter ─────────────────────────────────────────────────

    def can_buy(
        self,
        market: str,
        open_positions: List[str] = None,
    ) -> Tuple[bool, str]:
        """
        매수 가능 여부 확인

        Returns:
            (가능 여부, 사유)
        """
        now = time.time()

        # 1. 글로벌 차단 (BTC 급락)
        if now < self._global_block_until:
            remaining = int(self._global_block_until - now)
            return False, f"BTC 충격 차단 ({remaining//60}분 {remaining%60}초): {self._block_reason}"

        # 2. 마켓별 차단
        blocked = self._blocked_until.get(market, 0)
        if now < blocked:
            remaining = int(blocked - now)
            return False, f"{market} 개별 차단 ({remaining//60}분 {remaining%60}초)"

        # 3. BTC 현재 단기 추세 확인
        btc_trend_ok, btc_msg = self._check_btc_trend()
        if not btc_trend_ok:
            return False, btc_msg

        # 4. 변동성 스파이크 확인
        if self._is_volatility_spike(market):
            return False, f"{market} 변동성 스파이크 감지 → 매수 대기"

        # 5. 포트폴리오 상관관계 확인
        if open_positions:
            corr_ok, corr_msg = self._check_portfolio_correlation(
                market, open_positions
            )
            if not corr_ok:
                return False, corr_msg

        return True, "OK"

    def can_maintain_position(self, market: str) -> Tuple[bool, str]:
        """
        기존 포지션 유지 여부 (비상 청산 권고)
        Returns:
            (유지 권장, 사유)
        """
        if market == self.BTC_MARKET:
            return True, "BTC는 직접 청산 판단"

        btc_ret_1h = self._get_return(self.BTC_MARKET, window_seconds=3600)
        if btc_ret_1h is not None and btc_ret_1h < self.BTC_SHOCK_1H:
            return False, (
                f"BTC 1시간 급락 {btc_ret_1h:.2%} → {market} 청산 권고"
            )

        return True, "OK"

    # ── BTC Shock Detection ─────────────────────────────────────────

    def _check_btc_shock(self):
        """BTC 급락/급등 감지 → 글로벌 차단 설정"""
        ret_5m  = self._get_return(self.BTC_MARKET, window_seconds=300)
        ret_15m = self._get_return(self.BTC_MARKET, window_seconds=900)
        ret_1h  = self._get_return(self.BTC_MARKET, window_seconds=3600)

        now = time.time()

        # 심각 급락: 1시간 -5% 이상
        if ret_1h is not None and ret_1h < self.BTC_SHOCK_1H:
            self._global_block_until = now + self.BLOCK_DURATION_SEVERE
            self._block_reason = f"BTC 1h 급락 {ret_1h:.2%}"
            logger.warning(
                f"🚨 BTC 심각 급락 감지: {ret_1h:.2%} (1h) → "
                f"전체 매수 {self.BLOCK_DURATION_SEVERE//60}분 차단"
            )
            return

        # 일반 급락: 5분 -2.5% 이상
        if ret_5m is not None and ret_5m < self._btc_shock_threshold:
            self._global_block_until = now + self._block_duration
            self._block_reason = f"BTC 5m 급락 {ret_5m:.2%}"
            logger.warning(
                f"⚠️ BTC 급락 감지: {ret_5m:.2%} (5m) → "
                f"전체 매수 {self._block_duration//60}분 차단"
            )
            return

        # 15분 -3% 이상
        if ret_15m is not None and ret_15m < -0.03:
            self._global_block_until = now + self._block_duration
            self._block_reason = f"BTC 15m 하락 {ret_15m:.2%}"
            logger.warning(
                f"⚠️ BTC 15분 하락: {ret_15m:.2%} → 매수 차단"
            )

    def _check_btc_trend(self) -> Tuple[bool, str]:
        """BTC 단기 추세 확인 (매수 판단 시 호출)"""
        ret_5m = self._get_return(self.BTC_MARKET, window_seconds=300)
        if ret_5m is None:
            return True, "OK"  # 데이터 없으면 통과

        if ret_5m < self._btc_shock_threshold * 0.7:  # 70% 수준에서 경고
            return False, f"BTC 5분 하락 중 ({ret_5m:.2%}) → 매수 대기"

        return True, "OK"

    # ── Volatility Spike ────────────────────────────────────────────

    def _is_volatility_spike(self, market: str) -> bool:
        """변동성 스파이크 감지"""
        history = self._price_history.get(market)
        if not history or len(history) < 20:
            return False

        prices = np.array([p for _, p in history])
        returns = np.diff(prices) / prices[:-1]

        if len(returns) < 10:
            return False

        recent_vol = abs(returns[-1])
        avg_vol = np.std(returns[:-1])

        if avg_vol > 0 and recent_vol > avg_vol * self.VOL_SPIKE_MULT:
            logger.debug(
                f"⚡ 변동성 스파이크: {market} | "
                f"현재={recent_vol:.4f} > 평균={avg_vol:.4f} × {self.VOL_SPIKE_MULT}"
            )
            return True

        return False

    # ── Portfolio Correlation ───────────────────────────────────────

    def _check_portfolio_correlation(
        self,
        market: str,
        open_positions: List[str],
    ) -> Tuple[bool, str]:
        """
        포트폴리오 내 상관관계 확인
        BTC, ETH 등 고상관 코인 동시 3개 이상 보유 제한
        """
        # BTC/ETH/BNB 등 고상관 코인 그룹
        HIGH_CORR_GROUP = {
            "KRW-BTC", "KRW-ETH", "KRW-BNB", "KRW-SOL",
            "KRW-ADA", "KRW-DOT", "KRW-AVAX", "KRW-ATOM",
        }

        # 현재 포지션 중 고상관 그룹 수
        corr_count = sum(
            1 for pos in open_positions if pos in HIGH_CORR_GROUP
        )

        # 새로 추가할 코인도 고상관 그룹이면 체크
        if market in HIGH_CORR_GROUP and corr_count >= 6:
            return (
                False,
                f"고상관 포지션 한도 초과 ({corr_count}/3): "
                f"{', '.join(p for p in open_positions if p in HIGH_CORR_GROUP)}"
            )

        return True, "OK"

    # ── Helpers ─────────────────────────────────────────────────────

    def _get_return(self, market: str, window_seconds: int) -> Optional[float]:
        """
        특정 시간 창 내 수익률 계산

        Args:
            window_seconds: 기간 (초)
        Returns:
            수익률 (None = 데이터 부족)
        """
        history = self._price_history.get(market)
        if not history or len(history) < 2:
            return None

        now = time.time()
        cutoff = now - window_seconds

        # 창 시작 시점 가격 찾기
        start_price = None
        for ts, price in history:
            if ts >= cutoff:
                start_price = price
                break

        if start_price is None or start_price == 0:
            return None

        current_price = history[-1][1]
        return (current_price - start_price) / start_price

    def get_btc_status(self) -> Dict:
        """BTC 현재 상태 요약"""
        ret_5m  = self._get_return(self.BTC_MARKET, 300)
        ret_15m = self._get_return(self.BTC_MARKET, 900)
        ret_1h  = self._get_return(self.BTC_MARKET, 3600)
        now = time.time()

        is_blocked = now < self._global_block_until
        remaining = max(0, int(self._global_block_until - now))

        return {
            "is_globally_blocked": is_blocked,
            "block_remaining_sec": remaining,
            "block_reason": self._block_reason if is_blocked else "",
            "btc_ret_5m":  ret_5m,
            "btc_ret_15m": ret_15m,
            "btc_ret_1h":  ret_1h,
        }

    def force_unblock(self):
        """수동 차단 해제 (관리자용)"""
        self._global_block_until = 0
        self._blocked_until.clear()
        self._block_reason = ""
        logger.info("✅ 상관관계 필터 차단 수동 해제")
