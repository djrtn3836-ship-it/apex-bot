from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional
from loguru import logger


@dataclass
class MarketContext:
    regime: str                 # TRENDING_UP / TRENDING_DOWN / RANGING / VOLATILE
    volatility_rank: float      # 0~1
    volume_rank: float          # 0~1
    btc_correlation: float      # -1~1
    funding_pressure: float     # 0~1
    liquidity_score: float      # 0~1
    atr_percentile: float       # 0~1
    is_korean_session: bool     # 09:00~10:00 KST
    is_us_session: bool         # 22:00~23:00 KST


class MarketContextEngine:
    """모든 전략이 공유하는 시장 상태 판단 레이어"""

    def __init__(self):
        self._cache: dict = {}

    def analyze(self, df: pd.DataFrame, market: str = "") -> MarketContext:
        try:
            regime         = self._detect_regime(df)
            vol_rank       = self._volatility_rank(df)
            volume_rank    = self._volume_rank(df)
            atr_pct        = self._atr_percentile(df)
            funding        = self._funding_pressure(df)
            liquidity      = self._liquidity_score(df)
            session_kr, session_us = self._session_flags()
            btc_corr       = 0.5  # 기본값 (BTC 데이터 있으면 실계산)

            return MarketContext(
                regime=regime,
                volatility_rank=vol_rank,
                volume_rank=volume_rank,
                btc_correlation=btc_corr,
                funding_pressure=funding,
                liquidity_score=liquidity,
                atr_percentile=atr_pct,
                is_korean_session=session_kr,
                is_us_session=session_us,
            )
        except Exception as e:
            logger.warning(f"[Context] 분석 실패 {market}: {e}")
            return MarketContext("RANGING", 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, False, False)

    def _detect_regime(self, df: pd.DataFrame) -> str:
        if len(df) < 30:
            return "RANGING"
        close = df["close"].values
        high  = df["high"].values
        low   = df["low"].values

        # ADX 계산
        adx = self._adx(high, low, close, 14)

        # 볼린저 밴드 폭
        ma   = pd.Series(close).rolling(20).mean().iloc[-1]
        std  = pd.Series(close).rolling(20).std().iloc[-1]
        bb_width = (std * 2) / ma if ma > 0 else 0

        # ATR 비율
        atr = self._atr_value(high, low, close, 14)
        atr_ratio = atr / close[-1] if close[-1] > 0 else 0

        if adx > 30 and atr_ratio > 0.02:
            return "VOLATILE"
        if adx > 25:
            return "TRENDING_UP" if close[-1] > pd.Series(close).rolling(20).mean().iloc[-1] else "TRENDING_DOWN"
        return "RANGING"

    def _volatility_rank(self, df: pd.DataFrame) -> float:
        if len(df) < 20:
            return 0.5
        returns = df["close"].pct_change().dropna()
        current_vol = returns.iloc[-5:].std()
        hist_vol    = returns.std()
        if hist_vol == 0:
            return 0.5
        return float(min(current_vol / hist_vol, 1.0))

    def _volume_rank(self, df: pd.DataFrame) -> float:
        if len(df) < 20:
            return 0.5
        avg_vol  = df["volume"].rolling(20).mean().iloc[-1]
        curr_vol = df["volume"].iloc[-1]
        if avg_vol == 0:
            return 0.5
        return float(min(curr_vol / avg_vol, 2.0) / 2.0)

    def _atr_percentile(self, df: pd.DataFrame) -> float:
        if len(df) < 20:
            return 0.5
        high  = df["high"].values
        low   = df["low"].values
        close = df["close"].values
        atr_now  = self._atr_value(high, low, close, 14)
        atr_hist = np.mean([self._atr_value(high[i-14:i], low[i-14:i], close[i-14:i], 14)
                            for i in range(20, len(close), 5)])
        if atr_hist == 0:
            return 0.5
        return float(min(atr_now / atr_hist, 2.0) / 2.0)

    def _funding_pressure(self, df: pd.DataFrame) -> float:
        if len(df) < 10:
            return 0.5
        vol   = df["volume"].values[-10:]
        close = df["close"].values[-10:]
        op    = df["open"].values[-10:]
        delta = sum(v * (1 if c > o else -1) for v, c, o in zip(vol, close, op))
        total = sum(vol)
        if total == 0:
            return 0.5
        return float((delta / total + 1) / 2)

    def _liquidity_score(self, df: pd.DataFrame) -> float:
        if len(df) < 5:
            return 0.5
        spreads = ((df["high"] - df["low"]) / df["close"]).iloc[-5:]
        avg_spread = spreads.mean()
        return float(max(0.0, 1.0 - avg_spread * 20))

    def _session_flags(self):
        from datetime import datetime, timezone, timedelta
        kst = datetime.now(timezone(timedelta(hours=9)))
        h = kst.hour
        return (9 <= h < 10), (22 <= h < 23)

    def _adx(self, high, low, close, period=14) -> float:
        try:
            h = pd.Series(high)
            l = pd.Series(low)
            c = pd.Series(close)
            tr  = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
            atr = tr.rolling(period).mean()
            up  = h.diff()
            dn  = -l.diff()
            pdm = (up.where((up > dn) & (up > 0), 0)).rolling(period).mean()
            ndm = (dn.where((dn > up) & (dn > 0), 0)).rolling(period).mean()
            pdi = 100 * pdm / atr.replace(0, np.nan)
            ndi = 100 * ndm / atr.replace(0, np.nan)
            dx  = (100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan))
            adx = dx.rolling(period).mean()
            return float(adx.iloc[-1]) if not np.isnan(adx.iloc[-1]) else 20.0
        except Exception:
            return 20.0

    def _atr_value(self, high, low, close, period=14) -> float:
        try:
            h = pd.Series(high)
            l = pd.Series(low)
            c = pd.Series(close)
            tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
            return float(tr.rolling(period).mean().iloc[-1])
        except Exception:
            return 0.0
