from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, Tuple
from loguru import logger
from datetime import datetime
from strategies.base_strategy import BaseStrategy, Signal, SignalType
from strategies.v2.context.market_context import MarketContextEngine, MarketContext


@dataclass
class OrderImbalance:
    bid_volume: float
    ask_volume: float
    imbalance: float    # bid / (bid + ask), 0.5 이상이면 매수 우세
    spread_pct: float


class ATRChannelStrategy2(BaseStrategy):
    """
    ATR Channel 2.0 — 적응형 동적 채널 + 마켓 마이크로스트럭처
    레짐별 ATR 배수 자동 조정
    호가창 오더 임밸런스 필터 (매수세 > 매도세일 때만 진입)
    """
    NAME        = "ATR_Channel"
    BASE_CONF   = 0.55   # 기본 신뢰도 — config min_confidence(ATR_Channel)
    DESCRIPTION = "ATR 채널 2.0 — 레짐 적응형 + 오더 임밸런스 필터"
    VERSION     = "2.0"

    # 레짐별 ATR 배수
    MULT_VOLATILE = 3.0
    MULT_TRENDING = 2.0
    MULT_RANGING  = 1.5

    ATR_PERIOD           = 14
    CHANNEL_PERIOD       = 20
    MIN_IMBALANCE        = 0.48   # 최소 매수 임밸런스 (55% 이상)
    BREAKOUT_CONFIRM_BARS = 2     # 돌파 확인 캔들 수
    MIN_CONFIDENCE       = 0.45

    def _default_params(self) -> dict:
        return {"atr_period": 14, "channel_period": 20, "min_imbalance": 0.48, "min_confidence": 0.45}

    def __init__(self):
        super().__init__()
        self._context_engine = MarketContextEngine()

    def generate_signal(self, df: pd.DataFrame, market: str = "") -> Optional[Signal]:
        try:
            if not self._enabled:
                return None
            if len(df) < 40:
                return None

            ctx  = self._context_engine.analyze(df, market)
            mult = self._select_multiplier(ctx)

            channel_high, channel_low, atr = self._calc_channel(df, mult)
            if channel_high is None:
                return None

            imbalance = self._calc_order_imbalance(df)
            return self._evaluate(df, channel_high, channel_low, atr, imbalance, ctx, market, mult)

        except Exception as e:
            logger.warning(f"[ATR2.0] {market} 오류: {e}")
            return None

    def _select_multiplier(self, ctx: MarketContext) -> float:
        if ctx.regime == "VOLATILE":
            return self.MULT_VOLATILE
        elif ctx.regime == "RANGING":
            return self.MULT_RANGING
        return self.MULT_TRENDING

    def _calc_channel(
        self, df: pd.DataFrame, mult: float
    ) -> Tuple[Optional[float], Optional[float], float]:
        try:
            high  = df["high"]
            low   = df["low"]
            close = df["close"]

            tr  = pd.concat([
                high - low,
                (high - close.shift()).abs(),
                (low  - close.shift()).abs(),
            ], axis=1).max(axis=1)
            atr = tr.rolling(self.ATR_PERIOD).mean()
            ma  = close.rolling(self.CHANNEL_PERIOD).mean()

            ch_high = float((ma + mult * atr).iloc[-1])
            ch_low  = float((ma - mult * atr).iloc[-1])
            atr_val = float(atr.iloc[-1])

            return ch_high, ch_low, atr_val
        except Exception as _e:
            return None, None, 0.0

    def _calc_order_imbalance(self, df: pd.DataFrame) -> OrderImbalance:
        try:
            recent = df.iloc[-5:]
            bid_vol = float((recent["volume"] * (
                (recent["close"] - recent["low"]) /
                (recent["high"] - recent["low"]).replace(0, 0.001)
            )).sum())
            ask_vol = float((recent["volume"] * (
                (recent["high"] - recent["close"]) /
                (recent["high"] - recent["low"]).replace(0, 0.001)
            )).sum())
            total = bid_vol + ask_vol
            imbalance = bid_vol / total if total > 0 else 0.5
            spread = float(((recent["high"] - recent["low"]) / recent["close"]).mean())
            return OrderImbalance(bid_vol, ask_vol, imbalance, spread)
        except Exception as _e:
            return OrderImbalance(0, 0, 0.52, 0)  # 데이터 없을 때 기본 통과

    def _evaluate(
        self,
        df: pd.DataFrame,
        ch_high: float,
        ch_low: float,
        atr: float,
        imb: OrderImbalance,
        ctx: MarketContext,
        market: str,
        mult: float,
    ) -> Optional[Signal]:
        close = float(df["close"].iloc[-1])

        # 채널 하단 터치 후 반등 (평균 회귀)
        near_low = abs(close - ch_low) / ch_low < 0.008
        if not near_low:
            return None

        # 오더 임밸런스 필터
        if imb.imbalance < self.MIN_IMBALANCE:
            return None

        # 하락 추세 필터
        if ctx.regime == "TRENDING_DOWN":
            return None

        # 직전 확인 캔들 (채널 하단 근처에 머물렀는지)
        prev_lows = df["low"].iloc[-self.BREAKOUT_CONFIRM_BARS-1:-1]
        confirm   = any(abs(float(l) - ch_low) / ch_low < 0.015 for l in prev_lows)

        imb_bonus    = (imb.imbalance - self.MIN_IMBALANCE) * 2.0
        regime_bonus = 0.15 if ctx.regime == "RANGING" else 0.05
        confirm_bonus = 0.1 if confirm else 0.0
        mult_bonus   = 0.1  if mult == self.MULT_RANGING else 0.0

        confidence = min(
            0.40
            + imb_bonus
            + regime_bonus
            + confirm_bonus
            + mult_bonus
            + ctx.volume_rank * 0.1,
            1.0,
        )

        if confidence < self.MIN_CONFIDENCE:
            return None

        target = float(df["close"].rolling(self.CHANNEL_PERIOD).mean().iloc[-1])
        rr     = (target - close) / atr if atr > 0 else 0

        logger.info(
            f"[ATR2.0] 📉→📈 {market} 채널 하단 반등 | "
            f"채널=({ch_low:.0f}~{ch_high:.0f}) | "
            f"임밸런스={imb.imbalance:.2f} | "
            f"RR={rr:.2f} | 신뢰도={confidence:.2f}"
        )

        return Signal(
            signal=SignalType.BUY,
            confidence=confidence,
            strategy_name=self.NAME,
            market         = market,
            score          = confidence * 2.0 - 1.0,
            entry_price    = float(df["close"].iloc[-1]),
            stop_loss      = float(df["close"].iloc[-1]) * 0.978,
            take_profit    = float(df["close"].iloc[-1]) * 1.025,
            reason         = f"{self.NAME} v2 신호",
            timeframe      = "1h",
            timestamp      = datetime.now(),
            metadata={
                "channel_high": ch_high,
                "channel_low":  ch_low,
                "imbalance":    imb.imbalance,
                "atr":          atr,
                "multiplier":   mult,
                "rr":           rr,
                "regime":       ctx.regime,
            },
        )