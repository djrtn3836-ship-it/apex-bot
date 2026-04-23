from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, List, Tuple
from loguru import logger
from strategies.base_strategy import BaseStrategy, Signal, SignalType
from strategies.v2.context.market_context import MarketContextEngine, MarketContext


@dataclass
class DivergenceResult:
    div_type: str       # "regular" / "hidden"
    timeframe: str      # "15m" / "1h" / "4h"
    score: int          # 1~3 (몇개 TF에서 발생)
    rsi_value: float
    price_trend: str    # "up" / "down"


class RSIDivergenceStrategy2(BaseStrategy):
    """
    RSI 2.0 — 다중 시간프레임 히든 다이버전스
    3개 TF(15m/1h/4h) 동시 히든 다이버전스 감지
    히든 다이버전스 = 추세 지속 신호 (일반보다 승률 높음)
    RSI 50 레벨 지지/저항 필터 추가
    """
    NAME        = "RSI_Divergence"
    DESCRIPTION = "다중TF 히든 다이버전스 2.0 — 3중 시간프레임 합의"
    VERSION     = "2.0"

    RSI_PERIOD       = 14
    LOOKBACK_PIVOTS  = 20    # 피벗 탐색 기간
    MIN_SCORE        = 2     # 최소 TF 합의 수
    MIN_CONFIDENCE   = 0.45

    def __init__(self):
        super().__init__()
        self._context_engine = MarketContextEngine()

    def generate_signal(self, df: pd.DataFrame, market: str = "") -> Optional[Signal]:
        try:
            if not self._enabled:
                return None
            if len(df) < 60:
                return None

            ctx = self._context_engine.analyze(df, market)

            if ctx.regime == "VOLATILE" and ctx.volatility_rank > 0.8:
                return None

            # 다중 TF 시뮬레이션 (1h 기본 데이터로 4h/15m 근사)
            results = []
            results.append(self._check_divergence(df, "1h"))
            results.append(self._check_divergence_resample(df, 4, "4h"))
            results.append(self._check_divergence_resample(df, 4, "15m", downsample=False))

            valid   = [r for r in results if r is not None]
            score   = len(valid)

            if score < self.MIN_SCORE:
                return None

            rsi_now = self._calc_rsi(df["close"])

            # RSI 50 레벨 필터
            # 매수: RSI가 50 위에서 되돌림 후 반등 (40~55 구간)
            if not (38 <= rsi_now <= 58):
                return None

            regime_bonus = 0.2 if ctx.regime in ("TRENDING_UP", "RANGING") else 0.0
            score_bonus  = (score - self.MIN_SCORE) * 0.15
            rsi_bonus    = max(0, (55 - rsi_now) / 100)

            confidence = min(
                0.40
                + score_bonus
                + regime_bonus
                + rsi_bonus
                + ctx.volume_rank * 0.1,
                1.0,
            )

            if confidence < self.MIN_CONFIDENCE:
                return None

            div_types = [r.div_type for r in valid]
            hidden_count = div_types.count("hidden")

            logger.info(
                f"[RSI2.0] 🔄 {market} 다이버전스 score={score}/3 | "
                f"히든={hidden_count}개 | RSI={rsi_now:.1f} | "
                f"레짐={ctx.regime} | 신뢰도={confidence:.2f}"
            )

            return Signal(
                signal_type=SignalType.BUY,
                confidence=confidence,
                strategy_name=self.NAME,
                metadata={
                    "div_score":    score,
                    "hidden_count": hidden_count,
                    "rsi":          rsi_now,
                    "regime":       ctx.regime,
                    "timeframes":   [r.timeframe for r in valid],
                },
            )

        except Exception as e:
            logger.warning(f"[RSI2.0] {market} 오류: {e}")
            return None

    def _calc_rsi(self, close: pd.Series, period: int = 14) -> float:
        try:
            delta = close.diff()
            gain  = delta.where(delta > 0, 0.0).rolling(period).mean()
            loss  = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
            rs    = gain / loss.replace(0, np.nan)
            rsi   = 100 - (100 / (1 + rs))
            return float(rsi.iloc[-1])
        except Exception:
            return 50.0

    def _check_divergence(
        self, df: pd.DataFrame, tf: str
    ) -> Optional[DivergenceResult]:
        try:
            close = df["close"]
            rsi   = 100 - (100 / (1 + (
                close.diff().clip(lower=0).rolling(self.RSI_PERIOD).mean() /
                (-close.diff().clip(upper=0)).rolling(self.RSI_PERIOD).mean().replace(0, np.nan)
            )))

            # 피벗 로우 탐색 (최근 20개 캔들)
            n = min(self.LOOKBACK_PIVOTS, len(df) - 1)
            price_lows = []
            rsi_lows   = []

            for i in range(2, n - 2):
                idx = len(df) - 1 - i
                if idx < 2:
                    continue
                if (close.iloc[idx] < close.iloc[idx-1] and
                        close.iloc[idx] < close.iloc[idx+1]):
                    price_lows.append((idx, float(close.iloc[idx])))
                    rsi_lows.append((idx, float(rsi.iloc[idx])))

            if len(price_lows) < 2:
                return None

            p1_idx, p1_price = price_lows[-1]
            p2_idx, p2_price = price_lows[-2]
            r1_idx, r1_rsi   = rsi_lows[-1]
            r2_idx, r2_rsi   = rsi_lows[-2]

            # 히든 강세 다이버전스: 가격 저점 상승 + RSI 저점 하락
            hidden_bull = p1_price > p2_price and r1_rsi < r2_rsi

            # 일반 강세 다이버전스: 가격 저점 하락 + RSI 저점 상승
            regular_bull = p1_price < p2_price and r1_rsi > r2_rsi

            if hidden_bull:
                return DivergenceResult("hidden", tf, 1,
                                        float(rsi.iloc[-1]), "up")
            if regular_bull:
                return DivergenceResult("regular", tf, 1,
                                        float(rsi.iloc[-1]), "up")
            return None

        except Exception:
            return None

    def _check_divergence_resample(
        self,
        df: pd.DataFrame,
        factor: int,
        tf: str,
        downsample: bool = True,
    ) -> Optional[DivergenceResult]:
        try:
            if downsample:
                # 4시간봉 근사: factor개 캔들 합산
                agg = df.iloc[::factor].copy()
            else:
                # 15분봉 근사: 최근 데이터 더 세밀하게
                agg = df.iloc[-max(30, len(df)//2):].copy()

            if len(agg) < 20:
                return None

            return self._check_divergence(agg, tf)
        except Exception:
            return None
