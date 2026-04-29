from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, Tuple
from loguru import logger
from strategies.base_strategy import BaseStrategy, Signal, SignalType, safe_float, safe_last, safe_rolling_mean, safe_rolling_std, safe_div, kst_now
from strategies.v2.context.market_context import MarketContextEngine, MarketContext


@dataclass
class MACDState:
    macd: float
    signal: float
    hist: float
    hist_prev: float
    hist_prev2: float
    acceleration: float   # 2차 미분 (가속도)
    fast: int
    slow: int
    sig: int
    hist_list: list = None   # 최근 8봉 히스토그램 (골든크로스 탐색용)


class MACDCrossStrategy2(BaseStrategy):
    """
    MACD 2.0 — 동적 임계값 MACD
    변동성에 따라 파라미터 자동 전환
    히스토그램 가속도 필터 (가속 중인 크로스만 진입)
    """
    NAME        = "MACD_Cross"
    BASE_CONF   = 0.5   # 기본 신뢰도 — config min_confidence(MACD_Cross)
    DESCRIPTION = "동적 MACD 2.0 — 변동성 적응형 파라미터 + 가속도 필터"
    VERSION     = "2.0"

    # 변동성별 파라미터 세트
    PARAMS_HIGH   = {"fast": 5,  "slow": 13, "sig": 3}   # 고변동성
    PARAMS_MEDIUM = {"fast": 8,  "slow": 21, "sig": 5}   # 중간
    PARAMS_LOW    = {"fast": 12, "slow": 26, "sig": 9}   # 저변동성

    MIN_ACCELERATION  = 0.0     # 히스토그램 가속도 최소값
    MIN_VOLUME_RANK   = 0.30    # 최소 거래량 순위
    MIN_CONFIDENCE    = 0.45

    def _default_params(self) -> dict:
        return {"fast": 8, "slow": 21, "sig": 5, "min_confidence": 0.45}

    def __init__(self):
        super().__init__()
        self._context_engine = MarketContextEngine()

    def generate_signal(self, df: pd.DataFrame, market: str = "") -> Optional[Signal]:
        try:
            if not self._enabled:
                return None
            if df is None or len(df) < 50:
                return None

            ctx    = (self._context_engine.analyze(df, market)
                      if self._context_engine is not None else None)
            params = self._select_params(ctx)
            state  = self._calc_macd(df, params["fast"], params["slow"], params["sig"])

            if state is None:
                return None

            return self._evaluate(state, ctx, market)

        except Exception as e:
            logger.warning(f"[MACD2.0] {market} 오류: {e}")
            return None

    def _select_params(self, ctx: MarketContext) -> dict:
        if ctx.atr_percentile > 0.7:
            return self.PARAMS_HIGH
        elif ctx.atr_percentile < 0.3:
            return self.PARAMS_LOW
        return self.PARAMS_MEDIUM

    def _calc_macd(
        self, df: pd.DataFrame, fast: int, slow: int, sig: int
    ) -> Optional[MACDState]:
        try:
            close   = df["close"]
            ema_f   = close.ewm(span=fast,  adjust=False).mean()
            ema_s   = close.ewm(span=slow,  adjust=False).mean()
            macd    = ema_f - ema_s
            signal  = macd.ewm(span=sig, adjust=False).mean()
            hist    = macd - signal

            if len(hist) < 4:
                return None

            h0, h1, h2 = float(hist.iloc[-1]), float(hist.iloc[-2]), float(hist.iloc[-3])
            accel = h0 - 2 * h1 + h2   # 2차 미분

            return MACDState(
                macd=float(macd.iloc[-1]),
                signal=float(signal.iloc[-1]),
                hist=h0,
                hist_prev=h1,
                hist_prev2=h2,
                hist_list=[float(hist.iloc[-(k+1)]) for k in range(min(8, len(hist)))],
                acceleration=accel,
                fast=fast,
                slow=slow,
                sig=sig,
            )
        except Exception as _e:
            return None

    def _evaluate(
        self, state: MACDState, ctx: MarketContext, market: str
    ) -> Optional[Signal]:
        # 골든 크로스: 히스토그램 음→양 전환
        golden_cross = state.hist > 0 > state.hist_prev

        if not golden_cross:
            return None

        # 가속도 필터: 가속 중인 크로스만
        if state.acceleration < self.MIN_ACCELERATION:
            return None

        # 거래량 필터
        if ctx is None or ctx.volume_rank < self.MIN_VOLUME_RANK:
            return None

        # 하락 추세에서는 진입 금지
        if ctx is not None and ctx.regime == "TRENDING_DOWN":
            return None

        accel_bonus  = min(state.acceleration * 10, 0.2)
        regime_bonus = 0.15 if ctx is not None and ctx.regime == "TRENDING_UP" else 0.0
        vol_bonus    = (ctx.volume_rank * 0.1) if ctx is not None else 0.0
        param_bonus  = 0.1 if state.fast == 5 else 0.0  # 고변동성 파라미터 보너스

        confidence = min(
            0.40
            + accel_bonus
            + regime_bonus
            + vol_bonus
            + param_bonus,
            1.0,
        )

        if confidence < self.MIN_CONFIDENCE:
            return None

        logger.info(
            f"[MACD2.0] ⚡ {market} 골든크로스 | "
            f"파라미터=({state.fast},{state.slow},{state.sig}) | "
            f"가속도={state.acceleration:+.6f} | "
            f"레짐={ctx.regime} | 신뢰도={confidence:.2f}"
        )

        return Signal(
            signal=SignalType.BUY,
            confidence=confidence,
            strategy_name=self.NAME,
            market         = market,
            score          = confidence * 2.0 - 1.0,
            entry_price    = safe_last(df["close"]),
            stop_loss      = safe_last(df["close"]) * 0.978,
            take_profit    = safe_last(df["close"]) * 1.044,
            reason         = f"{self.NAME} v2 신호",
            timeframe      = "1h",
            timestamp      = kst_now(),
            metadata={
                "macd":         state.macd,
                "hist":         state.hist,
                "acceleration": state.acceleration,
                "params":       f"{state.fast},{state.slow},{state.sig}",
                "regime":       ctx.regime,
                "atr_pct":      ctx.atr_percentile,
            },
        )