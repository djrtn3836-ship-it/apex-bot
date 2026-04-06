"""
Apex Bot - 멀티 타임프레임 신호 합산기 (M3)
6개 TF 신호를 가중 합산하여 최종 방향 결정
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
from loguru import logger


class TFDirection(Enum):
    STRONG_UP   =  2
    UP          =  1
    NEUTRAL     =  0
    DOWN        = -1
    STRONG_DOWN = -2


@dataclass
class TFSignal:
    timeframe:  str
    direction:  TFDirection
    strength:   float          # 0.0 ~ 1.0
    ema_trend:  str            # "UP" / "DOWN" / "FLAT"
    rsi:        float
    weight:     float


@dataclass
class MTFResult:
    combined_score:  float
    final_direction: TFDirection
    allow_buy:       bool
    allow_sell:      bool
    tf_signals:      List[TFSignal] = field(default_factory=list)
    dominant_tf:     str = ""
    reason:          str = ""


# 타임프레임별 가중치 (상위 TF 중시)
TF_WEIGHTS: Dict[str, float] = {
    "1d":  0.35,
    "4h":  0.25,
    "1h":  0.20,
    "15m": 0.12,
    "5m":  0.05,
    "1m":  0.03,
}


class MTFSignalMerger:
    """멀티 타임프레임 신호 합산기"""

    def __init__(self, weights: Dict[str, float] = None):
        self.weights = weights or TF_WEIGHTS
        logger.info(f"✅ MTFSignalMerger 초기화 | TF: {list(self.weights.keys())}")

    def analyze(self, tf_dataframes: Dict[str, pd.DataFrame]) -> MTFResult:
        """
        tf_dataframes: {"1d": df_daily, "4h": df_4h, ...}
        각 df는 최소 close, ema_20, ema_50, ema_200, rsi 컬럼 필요
        """
        tf_signals = []

        for tf, df in tf_dataframes.items():
            if df is None or len(df) < 5:
                continue
            sig = self._analyze_single_tf(tf, df)
            tf_signals.append(sig)

        if not tf_signals:
            return MTFResult(
                combined_score  = 0.0,
                final_direction = TFDirection.NEUTRAL,
                allow_buy       = False,
                allow_sell      = False,
                reason          = "데이터 없음",
            )

        return self._merge(tf_signals)

    def _analyze_single_tf(self, tf: str, df: pd.DataFrame) -> TFSignal:
        last    = df.iloc[-1]
        close   = float(last.get("close", 0))
        ema20   = float(last.get("ema_20",  close))
        ema50   = float(last.get("ema_50",  close))
        ema200  = float(last.get("ema_200", close))
        rsi     = float(last.get("rsi",     50))
        weight  = self.weights.get(tf, 0.1)

        # 추세 방향
        if close > ema20 > ema50 > ema200:
            direction  = TFDirection.STRONG_UP
            ema_trend  = "UP"
            strength   = min(1.0, (close - ema200) / ema200 * 10)
        elif close > ema50:
            direction  = TFDirection.UP
            ema_trend  = "UP"
            strength   = 0.6
        elif close < ema20 < ema50 < ema200:
            direction  = TFDirection.STRONG_DOWN
            ema_trend  = "DOWN"
            strength   = min(1.0, (ema200 - close) / ema200 * 10)
        elif close < ema50:
            direction  = TFDirection.DOWN
            ema_trend  = "DOWN"
            strength   = 0.6
        else:
            direction  = TFDirection.NEUTRAL
            ema_trend  = "FLAT"
            strength   = 0.3

        return TFSignal(
            timeframe  = tf,
            direction  = direction,
            strength   = strength,
            ema_trend  = ema_trend,
            rsi        = rsi,
            weight     = weight,
        )

    def _merge(self, signals: List[TFSignal]) -> MTFResult:
        total_weight = sum(s.weight for s in signals)
        score        = sum(
            s.direction.value * s.strength * s.weight
            for s in signals
        ) / (total_weight or 1)

        # 일봉/4시간 추세 확인 (상위 TF 거부권)
        higher_tfs   = [s for s in signals if s.timeframe in ("1d", "4h")]
        higher_down  = any(s.direction in (TFDirection.DOWN, TFDirection.STRONG_DOWN)
                           for s in higher_tfs)
        higher_up    = any(s.direction in (TFDirection.UP, TFDirection.STRONG_UP)
                           for s in higher_tfs)

        allow_buy  = score > 0.3 and not higher_down
        allow_sell = score < -0.3 and not higher_up

        if score > 1.0:
            final = TFDirection.STRONG_UP
        elif score > 0.3:
            final = TFDirection.UP
        elif score < -1.0:
            final = TFDirection.STRONG_DOWN
        elif score < -0.3:
            final = TFDirection.DOWN
        else:
            final = TFDirection.NEUTRAL

        dominant = max(signals, key=lambda s: s.weight * abs(s.direction.value))

        return MTFResult(
            combined_score  = score,
            final_direction = final,
            allow_buy       = allow_buy,
            allow_sell      = allow_sell,
            tf_signals      = signals,
            dominant_tf     = dominant.timeframe,
            reason          = (
                f"MTF합산={score:.2f} | 지배TF={dominant.timeframe} | "
                f"BUY={'✅' if allow_buy else '❌'} SELL={'✅' if allow_sell else '❌'}"
            ),
        )
