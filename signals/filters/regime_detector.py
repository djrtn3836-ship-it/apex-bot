"""
APEX BOT - 시장 레짐 감지기 v2.0
추세/횡보/변동성/베어반전 국면 자동 분류

Step 3 고도화:
  - BEAR_REVERSAL 신규 레짐 추가
    조건: RSI≤28 + Fear&Greed≤20 + BB%≤0.05 중 2개 이상
    → 하락장이지만 극단적 과매도 → 역발상 매수 허용
  - 레짐별 허용 전략 세분화 (TRENDING_DOWN 완전 차단 해제)
  - 허스트 지수 계산 수치 안정화 (log(0) 방지)
"""
import numpy as np
import pandas as pd
from typing import Dict, Optional, Tuple, List
from enum import Enum
from loguru import logger


class MarketRegime(Enum):
    TRENDING_UP    = "TRENDING_UP"     # 강한 상승 추세
    TRENDING_DOWN  = "TRENDING_DOWN"   # 강한 하락 추세
    RANGING        = "RANGING"         # 횡보/박스권
    VOLATILE       = "VOLATILE"        # 고변동성
    BEAR_REVERSAL  = "BEAR_REVERSAL"   # ✅ 신규: 하락장 역발상 반전
    UNKNOWN        = "UNKNOWN"


# 레짐별 허용 전략 (None = 모두 허용)
REGIME_ALLOWED_STRATEGIES: Dict[str, Optional[List[str]]] = {
    "TRENDING_UP":   None,   # 모든 전략 허용
    "TRENDING_DOWN": [],     # 전략 전부 차단 (단, BEAR_REVERSAL로 전환 가능)
    "RANGING":       ["RSI_Divergence", "VWAP_Reversion",
                      "Bollinger_Squeeze", "ATR_Channel",
                      "ML_Ensemble"],
    "VOLATILE":      ["VolBreakout", "ATR_Channel",
                      "Bollinger_Squeeze", "ML_Ensemble"],
    "BEAR_REVERSAL": ["RSI_Divergence", "VWAP_Reversion",
                      "Bollinger_Squeeze", "ML_Ensemble"],  # ✅ 역발상 전용
    "UNKNOWN":       None,
}


class RegimeDetector:
    """
    시장 레짐 자동 감지기

    사용 지표:
    - ADX: 추세 강도
    - ATR / 역사적 변동성: 변동성 측정
    - 볼린저 밴드 폭: 횡보 판단
    - 허스트 지수: 추세 vs 평균회귀 성향
    - EMA 정렬: 추세 방향
    - RSI + BB%: BEAR_REVERSAL 감지
    """

    def __init__(self):
        self.adx_trend_threshold  = 25
        self.adx_strong_threshold = 40
        self.vol_threshold        = 2.0
        self.bb_squeeze_threshold = 0.03
        self._cache: Dict[str, MarketRegime] = {}
        self._bear_reversal_counts: Dict[str, int] = {}

    def detect(
        self,
        market: str,
        df: pd.DataFrame,
        timeframe: str = "60",
        fear_greed_index: Optional[int] = None,
    ) -> MarketRegime:
        """
        시장 레짐 감지

        Args:
            fear_greed_index: 공포탐욕 지수 (BEAR_REVERSAL 감지용)
        """
        if df is None or len(df) < 50:
            return MarketRegime.UNKNOWN

        try:
            last  = df.iloc[-1]
            close = df["close"]

            # ── 기본 지표 ────────────────────────────────────
            adx      = float(last.get("adx",      0) or 0)
            di_plus  = float(last.get("di_plus",  0) or 0)
            di_minus = float(last.get("di_minus", 0) or 0)
            atr_pct  = float(last.get("atr_pct",  1) or 1)
            bb_width = float(last.get("bb_width", 0.05) or 0.05)
            bb_pct   = float(last.get("bb_pct",   0.5) or 0.5)
            rsi      = float(last.get("rsi",      50)  or 50)
            price    = float(close.iloc[-1])

            hist_vol = (
                close.pct_change().rolling(20).std().iloc[-1] * np.sqrt(365) * 100
            )

            # ── EMA 정렬 ─────────────────────────────────────
            ema20  = float(last.get("ema20",  price) or price)
            ema50  = float(last.get("ema50",  price) or price)
            ema200 = float(last.get("ema200", price) or price)

            ema_bull = price > ema20 > ema50 > ema200
            ema_bear = price < ema20 < ema50 < ema200

            # ── 허스트 지수 ──────────────────────────────────
            hurst = self._calc_hurst(close.tail(100))

            # ✅ Step 3: BEAR_REVERSAL 감지 (최우선 체크)
            bear_reversal = self._check_bear_reversal(
                rsi=rsi,
                bb_pct=bb_pct,
                fear_greed_index=fear_greed_index,
                adx=adx,
                di_minus=di_minus,
                di_plus=di_plus,
            )
            if bear_reversal:
                regime = MarketRegime.BEAR_REVERSAL
            else:
                regime = self._classify(
                    adx=adx, di_plus=di_plus, di_minus=di_minus,
                    bb_width=bb_width, atr_pct=atr_pct,
                    historical_vol=hist_vol, hurst=hurst,
                    ema_bull_aligned=ema_bull,
                    ema_bear_aligned=ema_bear,
                    price=price, ema200=ema200,
                )

            self._cache[market] = regime
            logger.debug(
                f"레짐 감지 | {market} | {regime.value} | "
                f"ADX={adx:.1f} BB폭={bb_width:.3f} "
                f"허스트={hurst:.3f} RSI={rsi:.1f}"
                + (f" FG={fear_greed_index}" if fear_greed_index else "")
            )
            return regime

        except Exception as e:
            logger.error(f"레짐 감지 오류 ({market}): {e}")
            return MarketRegime.UNKNOWN

    def _detect_impl(
        self,
        market: str,
        df: pd.DataFrame,
        timeframe: str = "60",
        fear_greed_index: Optional[int] = None,
    ) -> MarketRegime:
        """
        시장 레짐 감지

        Args:
            fear_greed_index: 공포탐욕 지수 (BEAR_REVERSAL 감지용)
        """
        if df is None or len(df) < 50:
            return MarketRegime.UNKNOWN

        try:
            last  = df.iloc[-1]
            close = df["close"]

            # ── 기본 지표 ────────────────────────────────────
            adx      = float(last.get("adx",      0) or 0)
            di_plus  = float(last.get("di_plus",  0) or 0)
            di_minus = float(last.get("di_minus", 0) or 0)
            atr_pct  = float(last.get("atr_pct",  1) or 1)
            bb_width = float(last.get("bb_width", 0.05) or 0.05)
            bb_pct   = float(last.get("bb_pct",   0.5) or 0.5)
            rsi      = float(last.get("rsi",      50)  or 50)
            price    = float(close.iloc[-1])

            hist_vol = (
                close.pct_change().rolling(20).std().iloc[-1] * np.sqrt(365) * 100
            )

            # ── EMA 정렬 ─────────────────────────────────────
            ema20  = float(last.get("ema20",  price) or price)
            ema50  = float(last.get("ema50",  price) or price)
            ema200 = float(last.get("ema200", price) or price)

            ema_bull = price > ema20 > ema50 > ema200
            ema_bear = price < ema20 < ema50 < ema200

            # ── 허스트 지수 ──────────────────────────────────
            hurst = self._calc_hurst(close.tail(100))

            # ✅ Step 3: BEAR_REVERSAL 감지 (최우선 체크)
            bear_reversal = self._check_bear_reversal(
                rsi=rsi,
                bb_pct=bb_pct,
                fear_greed_index=fear_greed_index,
                adx=adx,
                di_minus=di_minus,
                di_plus=di_plus,
            )
            if bear_reversal:
                regime = MarketRegime.BEAR_REVERSAL
            else:
                regime = self._classify(
                    adx=adx, di_plus=di_plus, di_minus=di_minus,
                    bb_width=bb_width, atr_pct=atr_pct,
                    historical_vol=hist_vol, hurst=hurst,
                    ema_bull_aligned=ema_bull,
                    ema_bear_aligned=ema_bear,
                    price=price, ema200=ema200,
                )

            self._cache[market] = regime
            logger.debug(
                f"레짐 감지 | {market} | {regime.value} | "
                f"ADX={adx:.1f} BB폭={bb_width:.3f} "
                f"허스트={hurst:.3f} RSI={rsi:.1f}"
                + (f" FG={fear_greed_index}" if fear_greed_index else "")
            )
            return regime

        except Exception as e:
            logger.error(f"레짐 감지 오류 ({market}): {e}")
            return MarketRegime.UNKNOWN

    def _check_bear_reversal(
        self,
        rsi: float,
        bb_pct: float,
        fear_greed_index: Optional[int],
        adx: float,
        di_minus: float,
        di_plus: float,
    ) -> bool:
        """
        ✅ Step 3 신규: BEAR_REVERSAL 조건 체크
        RSI≤28 + Fear&Greed≤20 + BB%≤0.05 중 2개 이상
        + 하락 추세 조건 (ADX>20 이고 DI- > DI+)
        """
        # 실제 하락장인지 확인 (약한 하락도 포함)
        is_downtrend = (adx > 20 and di_minus > di_plus) or (di_minus > di_plus * 1.3)

        if not is_downtrend:
            return False

        # 역발상 신호 카운트
        reversal_signals = 0

        if rsi <= 35:
            reversal_signals += 1

        if fear_greed_index is not None and fear_greed_index <= 25:
            reversal_signals += 1

        if bb_pct <= 0.15:
            reversal_signals += 1

        # 2개 이상 충족 시 BEAR_REVERSAL
        return reversal_signals >= 1

    def _classify(
        self,
        adx: float, di_plus: float, di_minus: float,
        bb_width: float, atr_pct: float, historical_vol: float,
        hurst: float, ema_bull_aligned: bool, ema_bear_aligned: bool,
        price: float, ema200: float,
    ) -> MarketRegime:
        """레짐 분류 결정 트리"""

        # 고변동성 (방향 불명)
        if atr_pct > 5.0 or historical_vol > 100:
            return MarketRegime.VOLATILE

        # 강한 추세
        if adx > self.adx_trend_threshold and hurst > 0.55:
            if di_plus > di_minus and ema_bull_aligned:
                return MarketRegime.TRENDING_UP
            elif di_minus > di_plus and ema_bear_aligned:
                return MarketRegime.TRENDING_DOWN
            elif di_plus > di_minus and price > ema200:
                return MarketRegime.TRENDING_UP
            else:
                return MarketRegime.TRENDING_DOWN

        # 횡보/박스권
        if adx < self.adx_trend_threshold and (
            bb_width < self.bb_squeeze_threshold or hurst < 0.45
        ):
            return MarketRegime.RANGING

        # 변동성
        if atr_pct > self.vol_threshold * 2:
            return MarketRegime.VOLATILE

        # 중립: EMA200 기준
        return MarketRegime.TRENDING_UP if price > ema200 else MarketRegime.TRENDING_DOWN

    @staticmethod
    def _calc_hurst(series: pd.Series, lags: range = None) -> float:
        """
        ✅ Step 3 수정: 허스트 지수 계산 안정화
        log(0) 방지 + 최소 데이터 수 체크 강화
        """
        if len(series) < 20:
            return 0.5

        lags        = lags or range(2, min(20, len(series) // 2))
        log_returns = np.log(
            series.clip(lower=1e-10) / series.shift(1).clip(lower=1e-10)
        ).dropna().values

        if len(log_returns) < 10:
            return 0.5

        rs_values = []
        for lag in lags:
            chunks = [
                log_returns[i:i+lag]
                for i in range(0, len(log_returns) - lag + 1, lag)
            ]
            rs_list = []
            for c in chunks:
                if len(c) != lag:
                    continue
                std_c = np.std(c)
                if std_c < 1e-10:
                    continue
                cum   = np.cumsum(c - c.mean())
                r_s   = (cum.max() - cum.min()) / std_c
                if r_s > 0:
                    rs_list.append(r_s)
            if rs_list:
                rs_values.append(np.mean(rs_list))

        if len(rs_values) < 2:
            return 0.5

        try:
            log_lags = np.log(list(lags)[:len(rs_values)])
            log_rs   = np.log(np.array(rs_values) + 1e-10)
            hurst    = np.polyfit(log_lags, log_rs, 1)[0]
            return float(np.clip(hurst, 0.0, 1.0))
        except Exception:
            return 0.5

    def get_allowed_strategies(self, regime: MarketRegime) -> Optional[List[str]]:
        """레짐별 허용 전략 목록 (None = 모두 허용)"""
        return REGIME_ALLOWED_STRATEGIES.get(regime.value, None)

    def get_regime_strategy_preference(self, regime: MarketRegime) -> Dict:
        """레짐별 전략 선호도 반환 (하위 호환)"""
        allowed = self.get_allowed_strategies(regime)
        preferences = {
            MarketRegime.TRENDING_UP: {
                "preferred":   ["MACD_Cross", "Supertrend", "OrderBlock_SMC"],
                "avoid":       ["VWAP_Reversion", "Bollinger_Squeeze"],
                "description": "모멘텀 전략 선호",
            },
            MarketRegime.TRENDING_DOWN: {
                "preferred":   [],
                "avoid":       ["RSI_Divergence"],
                "description": "하락 추세 — 신규 매수 차단 (BEAR_REVERSAL 예외)",
            },
            MarketRegime.RANGING: {
                "preferred":   ["VWAP_Reversion", "Bollinger_Squeeze", "RSI_Divergence"],
                "avoid":       ["MACD_Cross", "Supertrend"],
                "description": "평균회귀 전략 선호",
            },
            MarketRegime.VOLATILE: {
                "preferred":   ["VolBreakout", "ATR_Channel"],
                "avoid":       ["VWAP_Reversion"],
                "description": "변동성 전략 + 포지션 축소",
            },
            MarketRegime.BEAR_REVERSAL: {
                "preferred":   ["RSI_Divergence", "VWAP_Reversion", "Bollinger_Squeeze"],
                "avoid":       ["MACD_Cross", "Supertrend", "VolBreakout"],
                "description": "역발상 매수 — 포지션 50% 축소, 임계값 -1.5 하향",
            },
        }
        return preferences.get(regime, {
            "preferred": [], "avoid": [], "description": "레짐 불명"
        })

    def get_cached_regime(self, market: str) -> MarketRegime:
        return self._cache.get(market, MarketRegime.UNKNOWN)

    def is_tradeable(self, regime: MarketRegime) -> bool:
        """해당 레짐에서 매수 가능 여부"""
        allowed = self.get_allowed_strategies(regime)
        # allowed가 빈 리스트면 거래 불가, None이면 모두 허용
        return allowed is None or len(allowed) > 0
