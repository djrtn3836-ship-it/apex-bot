from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, Tuple
from loguru import logger
from datetime import datetime
from strategies.base_strategy import BaseStrategy, Signal, SignalType, safe_float, safe_last
from strategies.v2.context.market_context import MarketContextEngine, MarketContext


@dataclass
class VolumeProfile:
    poc: float          # Point of Control (거래량 최다 가격대)
    vah: float          # Value Area High (상위 70% 거래량 상단)
    val: float          # Value Area Low (상위 70% 거래량 하단)
    total_volume: float


class VolBreakoutStrategy2(BaseStrategy):
    """
    VolBreakout 2.0 — 거래량 클러스터 돌파
    거래량 프로파일 POC/VAH/VAL 기반 의미있는 돌파만 진입
    가짜 돌파 필터 + 되돌림 확인 후 진입
    """
    NAME        = "VolBreakout"
    BASE_CONF   = 0.72   # 기본 신뢰도 — config min_confidence(Vol_Breakout)
    DESCRIPTION = "거래량 클러스터 돌파 2.0 — POC/VAH/VAL 기반"
    VERSION     = "2.0"

    # 파라미터
    VP_LOOKBACK        = 20      # 거래량 프로파일 계산 기간 (일)
    VP_BINS            = 50      # 가격 구간 분할 수
    VALUE_AREA_PCT     = 0.70    # Value Area 범위 (70%)
    BREAKOUT_MIN_VOL = 2.0     # 돌파 캔들 최소 거래량 배수
    BREAKOUT_MIN_PCT   = 0.003   # 최소 돌파 비율 0.3%
    RETEST_TOLERANCE   = 0.005   # 되돌림 허용 오차 0.5%
    MAX_CANDLES_WAIT   = 5       # 되돌림 대기 최대 캔들 수

    def _default_params(self) -> dict:
        return {"vp_lookback": 20, "vp_bins": 50, "breakout_min_vol": 2.5}

    def __init__(self):
        super().__init__()
        self._context_engine = MarketContextEngine()
        self._breakout_cache: dict = {}  # market -> breakout state

    def generate_signal(self, df: pd.DataFrame, market: str = "") -> Optional[Signal]:
        try:
            if not self._enabled:
                return None
            if len(df) < self.VP_LOOKBACK + 10:
                return None

            ctx = self._context_engine.analyze(df, market)

            # 횡보장에서는 돌파 전략 효과 없음
            if ctx.regime == "RANGING" and ctx.volatility_rank < 0.2:
                return None

            vp = self._calc_volume_profile(df)
            if vp is None:
                return None

            signal = self._check_breakout(df, vp, ctx, market)
            return signal

        except Exception as e:
            logger.warning(f"[VB2.0] {market} 오류: {e}")
            return None

    def _calc_volume_profile(self, df: pd.DataFrame) -> Optional[VolumeProfile]:
        try:
            recent = df.iloc[-self.VP_LOOKBACK:]
            price_min = float(recent["low"].min())
            price_max = float(recent["high"].max())
            if price_max <= price_min:
                return None

            bins = np.linspace(price_min, price_max, self.VP_BINS + 1)
            vol_profile = np.zeros(self.VP_BINS)

            for _, row in recent.iterrows():
                lo, hi, vol = row["low"], row["high"], row["volume"]
                for j in range(self.VP_BINS):
                    overlap = min(hi, bins[j+1]) - max(lo, bins[j])
                    if overlap > 0:
                        range_size = hi - lo if hi > lo else 1
                        vol_profile[j] += vol * overlap / range_size

            poc_idx = int(np.argmax(vol_profile))
            poc     = float((bins[poc_idx] + bins[poc_idx + 1]) / 2)

            # Value Area 계산 (전체 거래량의 70%)
            total_vol    = float(np.sum(vol_profile))
            target_vol   = total_vol * self.VALUE_AREA_PCT
            sorted_idx   = np.argsort(vol_profile)[::-1]
            cumvol       = 0.0
            va_indices   = []
            for idx in sorted_idx:
                cumvol += vol_profile[idx]
                va_indices.append(idx)
                if cumvol >= target_vol:
                    break

            vah = float(bins[max(va_indices) + 1])
            val = float(bins[min(va_indices)])

            return VolumeProfile(poc=poc, vah=vah, val=val, total_volume=total_vol)

        except Exception as e:
            logger.warning(f"[VB2.0] 거래량 프로파일 계산 실패: {e}")
            return None

    def _check_breakout(
        self,
        df: pd.DataFrame,
        vp: VolumeProfile,
        ctx: MarketContext,
        market: str,
    ) -> Optional[Signal]:
        close  = safe_last(df["close"])
        volume = safe_last(df["volume"])
        avg_vol = float(df["volume"].rolling(20).mean().iloc[-1])
        vol_ratio = volume / avg_vol if avg_vol > 0 else 0

        # VAH 상향 돌파 확인
        vah_breakout = (
            close > vp.vah * (1 + self.BREAKOUT_MIN_PCT)
            and vol_ratio >= self.BREAKOUT_MIN_VOL
        )

        if not vah_breakout:
            return None

        # 가짜 돌파 필터: 직전 3캔들 확인
        prev_closes = df["close"].iloc[-4:-1].values
        if not all(c <= vp.vah * (1 + self.BREAKOUT_MIN_PCT * 2) for c in prev_closes):
            # 이미 돌파 상태였으면 새 신호 아님
            return None

        # 되돌림 확인 (VAH가 지지선으로 전환됐는지)
        recent_low = float(df["low"].iloc[-3:].min())
        retest_ok  = recent_low >= vp.vah * (1 - self.RETEST_TOLERANCE)

        # 레짐 보정
        regime_bonus = 0.2 if ctx.regime in ("TRENDING_UP", "VOLATILE") else 0.0

        confidence = min(
            0.4
            + (vol_ratio / self.BREAKOUT_MIN_VOL) * 0.3
            + (0.1 if retest_ok else 0.0)
            + regime_bonus
            + ctx.volume_rank * 0.1,
            1.0,
        )

        if confidence < 0.45:
            return None

        logger.info(
            f"[VB2.0] 🚀 {market} VAH 돌파 | "
            f"VAH={vp.vah:.0f} POC={vp.poc:.0f} | "
            f"거래량배수={vol_ratio:.1f}x | "
            f"되돌림확인={retest_ok} | "
            f"신뢰도={confidence:.2f}"
        )

        return Signal(
            signal=SignalType.BUY,
            confidence=confidence,
            strategy_name=self.NAME,
            market         = market,
            score          = confidence * 2.0 - 1.0,
            entry_price    = safe_last(df["close"]),
            stop_loss      = safe_last(df["close"]) * 0.97,
            take_profit    = safe_last(df["close"]) * 1.06,
            reason         = f"{self.NAME} v2 신호",
            timeframe      = "1h",
            timestamp      = datetime.now(),
            metadata={
                "poc": vp.poc,
                "vah": vp.vah,
                "val": vp.val,
                "vol_ratio": vol_ratio,
                "retest_ok": retest_ok,
                "regime": ctx.regime,
            },
        )