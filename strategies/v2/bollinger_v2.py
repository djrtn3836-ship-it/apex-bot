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
class SqueezeState:
    is_squeezed: bool       # BB가 KC 안에 있는지
    bars_squeezed: int      # 압축 지속 캔들 수
    bars_since_break: int   # 폭발 후 경과 캔들 수
    delta_flow: float       # 압축 기간 누적 델타 오더플로우
    momentum: float         # 폭발 방향 모멘텀


class BollingerSqueezeStrategy2(BaseStrategy):
    """
    Bollinger Squeeze 2.0 — 밴드 압축 폭발 + 방향성 필터
    TTM Squeeze: BB가 KC 안에 완전히 들어올 때 압축 상태
    압축 중 누적 델타 오더플로우로 방향 예측
    폭발 후 첫 3캔들 내 진입만 허용
    """
    NAME        = "Bollinger_Squeeze"
    BASE_CONF   = 0.55   # 기본 신뢰도 — config min_confidence(Bollinger_Squeeze)
    DESCRIPTION = "TTM Squeeze 2.0 — 델타 오더플로우 방향 필터"
    VERSION     = "2.0"

    # 볼린저 밴드
    BB_PERIOD     = 20
    BB_STD        = 2.0
    # 켈트너 채널
    KC_PERIOD     = 20
    KC_ATR_MULT   = 1.5
    # 진입 조건
    MAX_BARS_AFTER_BREAK = 3      # 폭발 후 최대 진입 가능 캔들
    MIN_SQUEEZE_BARS     = 3      # 최소 압축 지속 캔들
    MIN_DELTA_FLOW       = 0.0    # 최소 델타 오더플로우
    MIN_CONFIDENCE       = 0.45

    def _default_params(self) -> dict:
        return {"bb_period": 20, "bb_std": 2.0, "kc_atr_mult": 1.5, "min_confidence": 0.45}

    def __init__(self):
        super().__init__()
        self._context_engine = MarketContextEngine()

    def generate_signal(self, df: pd.DataFrame, market: str = "") -> Optional[Signal]:
        try:
            if not self._enabled:
                return None
            if len(df) < 40:
                return None

            ctx   = self._context_engine.analyze(df, market)
            state = self._calc_squeeze_state(df)

            if state is None:
                return None

            return self._evaluate(state, ctx, market, df)

        except Exception as e:
            logger.warning(f"[BB2.0] {market} 오류: {e}")
            return None

    def _calc_squeeze_state(self, df: pd.DataFrame) -> Optional[SqueezeState]:
        try:
            close  = df["close"]
            high   = df["high"]
            low    = df["low"]
            volume = df["volume"]
            n      = len(df)

            # 볼린저 밴드
            bb_ma  = close.rolling(self.BB_PERIOD).mean()
            bb_std = close.rolling(self.BB_PERIOD).std()
            bb_upper = bb_ma + self.BB_STD * bb_std
            bb_lower = bb_ma - self.BB_STD * bb_std

            # 켈트너 채널
            kc_ma  = close.rolling(self.KC_PERIOD).mean()
            tr     = pd.concat([
                high - low,
                (high - close.shift()).abs(),
                (low  - close.shift()).abs(),
            ], axis=1).max(axis=1)
            kc_atr   = tr.rolling(self.KC_PERIOD).mean()
            kc_upper = kc_ma + self.KC_ATR_MULT * kc_atr
            kc_lower = kc_ma - self.KC_ATR_MULT * kc_atr

            # 압축 상태 판단 (BB가 KC 안에 완전히 있는지)
            squeezed = (bb_upper < kc_upper) & (bb_lower > kc_lower)

            # 현재 압축 상태 및 지속 기간
            cur_squeezed  = bool(squeezed.iloc[-1])
            bars_squeezed = 0
            for i in range(1, min(50, n)):
                if squeezed.iloc[-i]:
                    bars_squeezed += 1
                else:
                    break

            # 폭발 후 경과 캔들
            bars_since_break = 0
            if not cur_squeezed:
                for i in range(1, min(10, n)):
                    if not squeezed.iloc[-i]:
                        bars_since_break += 1
                    else:
                        break

            # 압축 기간 누적 델타 오더플로우
            squeeze_start = max(0, n - bars_squeezed - 1) if cur_squeezed else max(0, n - bars_since_break - bars_squeezed - 1)
            seg_close  = close.iloc[squeeze_start:]
            seg_open   = df["open"].iloc[squeeze_start:]
            seg_high   = high.iloc[squeeze_start:]
            seg_low    = low.iloc[squeeze_start:]
            seg_vol    = volume.iloc[squeeze_start:]

            range_size = (seg_high - seg_low).replace(0, 0.001)
            delta = (seg_vol * (seg_close - seg_open) / range_size).sum()

            # 모멘텀 (폭발 방향)
            momentum = float(close.iloc[-1] - close.iloc[-4]) if n >= 4 else 0.0

            return SqueezeState(
                is_squeezed=cur_squeezed,
                bars_squeezed=bars_squeezed,
                bars_since_break=bars_since_break,
                delta_flow=float(delta),
                momentum=momentum,
            )

        except Exception as e:
            logger.warning(f"[BB2.0] squeeze 계산 실패: {e}")
            return None

    def _evaluate(
        self,
        state: SqueezeState,
        ctx: MarketContext,
        market: str,
        df: pd.DataFrame,
    ) -> Optional[Signal]:
        # 폭발 후 3캔들 이내만 진입
        if state.is_squeezed:
            return None
        if state.bars_since_break > self.MAX_BARS_AFTER_BREAK:
            return None
        if state.bars_squeezed < self.MIN_SQUEEZE_BARS:
            return None

        # 방향성 필터: 델타 오더플로우 양수 + 모멘텀 양수
        if state.delta_flow <= self.MIN_DELTA_FLOW:
            return None
        if state.momentum <= 0:
            return None

        # 하락 추세 필터
        if ctx.regime == "TRENDING_DOWN":
            return None

        squeeze_bonus = min(state.bars_squeezed / 20, 0.2)
        delta_bonus   = min(abs(state.delta_flow) / 1e8, 0.15)
        regime_bonus  = 0.15 if ctx.regime in ("RANGING", "VOLATILE") else 0.05
        timing_bonus  = (self.MAX_BARS_AFTER_BREAK - state.bars_since_break) * 0.05

        confidence = min(
            0.40
            + squeeze_bonus
            + delta_bonus
            + regime_bonus
            + timing_bonus
            + ctx.volume_rank * 0.1,
            1.0,
        )

        if confidence < self.MIN_CONFIDENCE:
            return None

        logger.info(
            f"[BB2.0] 💥 {market} 스퀴즈 폭발 | "
            f"압축={state.bars_squeezed}봉 | "
            f"폭발후={state.bars_since_break}봉 | "
            f"델타플로우={state.delta_flow:+.0f} | "
            f"신뢰도={confidence:.2f}"
        )

        return Signal(
            signal=SignalType.BUY,
            confidence=confidence,
            strategy_name=self.NAME,
            market         = market,
            score          = confidence * 2.0 - 1.0,
            entry_price    = safe_last(df["close"]),
            stop_loss      = safe_last(df["close"]) * 0.98,
            take_profit    = safe_last(df["close"]) * 1.04,
            reason         = f"{self.NAME} v2 신호",
            timeframe      = "1h",
            timestamp      = datetime.now(),
            metadata={
                "bars_squeezed":    state.bars_squeezed,
                "bars_since_break": state.bars_since_break,
                "delta_flow":       state.delta_flow,
                "momentum":         state.momentum,
                "regime":           ctx.regime,
            },
        )