from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, Tuple
from loguru import logger
from strategies.base_strategy import BaseStrategy, Signal, SignalType, safe_float, safe_last, safe_rolling_mean, safe_rolling_std, safe_div, kst_now
from strategies.v2.context.market_context import MarketContextEngine, MarketContext


@dataclass
class SupertrendResult:
    direction: str    # "UP" / "DOWN"
    value: float
    just_flipped: bool  # 이번 캔들에서 방향 전환 여부


class SupertrendStrategy2(BaseStrategy):
    """
    Supertrend 2.0 — 다중 Supertrend 합의 시스템
    3개 파라미터 Supertrend 동시 합의 + 되돌림 진입
    단순 크로스가 아닌 방향전환 후 첫 되돌림에서 진입
    """
    NAME        = "Supertrend"
    BASE_CONF   = 0.6   # 기본 신뢰도 — config min_confidence(Supertrend)
    DESCRIPTION = "다중 Supertrend 합의 2.0 — 3중 필터 + 되돌림 진입"
    VERSION     = "2.0"

    # 3개 Supertrend 파라미터 (빠름/중간/느림)
    ST_PARAMS = [
        {"period": 7,  "multiplier": 1.5},
        {"period": 14, "multiplier": 2.0},
        {"period": 21, "multiplier": 3.0},
    ]

    MIN_CONSENSUS     = 2      # 최소 합의 개수 (2개 이상)
    PULLBACK_MAX_PCT  = 0.015  # 되돌림 최대 허용 비율 1.5%
    MIN_VOLUME_RANK   = 0.25   # 최소 거래량 순위

    def _default_params(self) -> dict:
        return {"min_consensus": 2, "pullback_max_pct": 0.015, "min_volume_rank": 0.25}

    def __init__(self):
        super().__init__()
        self._context_engine  = MarketContextEngine()
        self._prev_directions: dict = {}  # market -> [dir1, dir2, dir3]

    def generate_signal(self, df: pd.DataFrame, market: str = "") -> Optional[Signal]:
        try:
            if not self._enabled:
                return None
            if df is None or len(df) < 50:
                return None

            ctx = self._context_engine.analyze(df, market)

            # 횡보장에서는 추세 전략 비활성
            if ctx.regime == "RANGING":
                return None

            results = [
                self._calc_supertrend(df, p["period"], p["multiplier"])
                for p in self.ST_PARAMS
            ]

            signal = self._evaluate_consensus(df, results, ctx, market)
            return signal

        except Exception as e:
            logger.warning(f"[ST2.0] {market} 오류: {e}")
            return None

    def _calc_supertrend(
        self, df: pd.DataFrame, period: int, multiplier: float
    ) -> SupertrendResult:
        high  = df["high"].values
        low   = df["low"].values
        close = df["close"].values
        n     = len(close)

        # ATR 계산
        tr = np.zeros(n)
        for i in range(1, n):
            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i-1]),
                abs(low[i]  - close[i-1]),
            )
        atr = safe_rolling_mean(pd.Series(tr), period).values

        # Supertrend 계산
        upper = (high + low) / 2 + multiplier * atr
        lower = (high + low) / 2 - multiplier * atr

        st        = np.zeros(n)
        direction = np.zeros(n)  # 1=UP, -1=DOWN

        for i in range(1, n):
            if np.isnan(atr[i]):
                continue

            # 상단 밴드
            upper[i] = min(upper[i], upper[i-1]) if close[i-1] <= upper[i-1] else upper[i]
            # 하단 밴드
            lower[i] = max(lower[i], lower[i-1]) if close[i-1] >= lower[i-1] else lower[i]

            if st[i-1] == upper[i-1]:
                st[i]        = upper[i] if close[i] <= upper[i] else lower[i]
                direction[i] = -1       if close[i] <= upper[i] else 1
            else:
                st[i]        = lower[i] if close[i] >= lower[i] else upper[i]
                direction[i] = 1        if close[i] >= lower[i] else -1

        cur_dir  = "UP" if direction[-1] == 1 else "DOWN"
        prev_dir = "UP" if direction[-2] == 1 else "DOWN"
        flipped  = cur_dir != prev_dir

        return SupertrendResult(
            direction=cur_dir,
            value=float(st[-1]),
            just_flipped=flipped,
        )

    def _evaluate_consensus(
        self,
        df: pd.DataFrame,
        results: list,
        ctx: MarketContext,
        market: str,
    ) -> Optional[Signal]:
        up_count   = sum(1 for r in results if r.direction == "UP")
        down_count = sum(1 for r in results if r.direction == "DOWN")
        flips      = sum(1 for r in results if r.just_flipped)

        if up_count < self.MIN_CONSENSUS:
            return None

        current_price = safe_last(df["close"])
        avg_vol       = safe_last(safe_rolling_mean(df["volume"], 20))
        curr_vol      = safe_last(df["volume"])

        if ctx.volume_rank < self.MIN_VOLUME_RANK:
            return None

        # 되돌림 확인: 최근 고점에서 1.5% 이내 하락 후 반등
        recent_high   = float(df["high"].iloc[-5:].max())
        pullback_pct  = (recent_high - current_price) / recent_high if recent_high > 0 else 1.0

        is_pullback = 0.001 <= pullback_pct <= self.PULLBACK_MAX_PCT

        # 신뢰도 계산
        consensus_bonus = (up_count - self.MIN_CONSENSUS) * 0.15
        flip_bonus      = flips * 0.1
        pullback_bonus  = 0.15 if is_pullback else 0.0
        regime_bonus    = 0.2  if ctx.regime == "TRENDING_UP" else 0.0

        confidence = min(
            0.40
            + consensus_bonus
            + flip_bonus
            + pullback_bonus
            + regime_bonus
            + ctx.volume_rank * 0.1,
            1.0,
        )

        if confidence < 0.45:
            return None

        position_size = 1.0 if up_count == 3 else 0.5

        logger.info(
            f"[ST2.0] 📈 {market} 합의={up_count}/3 | "
            f"전환={flips}개 | 되돌림={is_pullback} | "
            f"포지션={position_size:.0%} | 신뢰도={confidence:.2f}"
        )

        return Signal(
            signal=SignalType.BUY,
            confidence=confidence,
            strategy_name=self.NAME,
            market         = market,
            score          = confidence * 2.0 - 1.0,
            entry_price    = safe_last(df["close"]),
            stop_loss      = safe_last(df["close"]) * 0.975,
            take_profit    = safe_last(df["close"]) * 1.05,
            reason         = f"{self.NAME} v2 신호",
            timeframe      = "1h",
            timestamp      = kst_now(),
            metadata={
                "consensus": up_count,
                "flips": flips,
                "is_pullback": is_pullback,
                "position_size": position_size,
                "regime": ctx.regime,
                "st_fast":   results[0].direction,
                "st_medium": results[1].direction,
                "st_slow":   results[2].direction,
            },
        )