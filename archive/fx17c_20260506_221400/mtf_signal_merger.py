"""Apex Bot -     (M3)
6 TF

수정 이력:
  [MTF-1] ema_20/ema_50/ema_200 → ema20/ema50/ema200
          candle_processor.py 컬럼명 기준으로 통일
          (수정 전: 항상 NEUTRAL 고착 → 매수 과도 억제)
  [MTF-2] f-string 중첩 따옴표 → 변수 분리
          (수정 전: Python 3.11 이하 SyntaxError 위험)
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
    mtf_aligned:     bool = False   # 상위/하위 TF 방향 일치 여부


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
    """MTFSignalMerger 클래스"""

    def __init__(self, weights: Dict[str, float] = None):
        self.weights = weights or TF_WEIGHTS
        logger.info(f" MTFSignalMerger  | TF: {list(self.weights.keys())}")

    def analyze(self, tf_dataframes: Dict[str, pd.DataFrame]) -> MTFResult:
        """tf_dataframes: {"1d": df_daily, "4h": df_4h, ...}
         df  close, ema20, ema50, ema200, rsi"""
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
        last   = df.iloc[-1]
        close  = float(last.get("close", 0))

        # [MTF-1 FIX] candle_processor.py 기준 컬럼명으로 통일
        # 이전: ema_20, ema_50, ema_200 (언더스코어) → 항상 close fallback → NEUTRAL 고착
        # 수정: ema20, ema50, ema200 (언더스코어 없음)
        ema20  = float(last.get("ema20",  close))
        ema50  = float(last.get("ema50",  close))
        ema200 = float(last.get("ema200", close))
        rsi    = float(last.get("rsi",    50))
        weight = self.weights.get(tf, 0.1)

        # 추세 방향
        if close > ema20 > ema50 > ema200:
            direction = TFDirection.STRONG_UP
            ema_trend = "UP"
            strength  = min(1.0, (close - ema200) / (ema200 + 1e-9) * 10)
        elif close > ema50:
            direction = TFDirection.UP
            ema_trend = "UP"
            strength  = 0.6
        elif close < ema20 < ema50 < ema200:
            direction = TFDirection.STRONG_DOWN
            ema_trend = "DOWN"
            strength  = min(1.0, (ema200 - close) / (ema200 + 1e-9) * 10)
        elif close < ema50:
            direction = TFDirection.DOWN
            ema_trend = "DOWN"
            strength  = 0.6
        else:
            direction = TFDirection.NEUTRAL
            ema_trend = "FLAT"
            strength  = 0.3

        return TFSignal(
            timeframe = tf,
            direction = direction,
            strength  = strength,
            ema_trend = ema_trend,
            rsi       = rsi,
            weight    = weight,
        )

    def _merge(self, signals: List[TFSignal]) -> MTFResult:
        total_weight = sum(s.weight for s in signals)
        score        = sum(
            s.direction.value * s.strength * s.weight
            for s in signals
        ) / (total_weight or 1)

        # 상위 TF 거부권 (1d, 4h)
        higher_tfs  = [s for s in signals if s.timeframe in ("1d", "4h")]
        higher_down = any(
            s.direction in (TFDirection.DOWN, TFDirection.STRONG_DOWN)
            for s in higher_tfs
        )
        higher_up   = any(
            s.direction in (TFDirection.UP, TFDirection.STRONG_UP)
            for s in higher_tfs
        )

        # 중간 TF 합의 체크 (1h, 15m)
        mid_tfs       = [s for s in signals if s.timeframe in ("1h", "15m")]
        mid_up_count  = sum(
            1 for s in mid_tfs
            if s.direction in (TFDirection.UP, TFDirection.STRONG_UP)
        )
        mid_agreement = (
            mid_up_count >= len(mid_tfs) * 0.5
            if mid_tfs else True
        )

        # RSI 과매도/과매수 보너스
        rsi_values = [s.rsi for s in signals if s.rsi > 0]
        avg_rsi    = sum(rsi_values) / len(rsi_values) if rsi_values else 50
        rsi_bonus  = 0.2 if avg_rsi < 35 else (-0.1 if avg_rsi > 70 else 0)
        score      = score + rsi_bonus

        # TF 수 보너스 (더 많은 TF 동의할수록 신뢰도 상승)
        tf_count  = len(signals)
        tf_bonus  = min(0.15, tf_count * 0.025)
        score     = score + (tf_bonus if score > 0 else -tf_bonus)

        # [FX16-1] BULL 레짐 RSI 과매도 soft-fail 확장
        # global_regime 은 인스턴스 속성 또는 호출부에서 주입
        _regime_str = str(getattr(self, '_global_regime', '') or '').upper()
        _is_bull_regime = any(k in _regime_str for k in ('BULL', 'TRENDING_UP', 'RECOVERY'))
        _rsi_oversold   = avg_rsi <= 25
        if _is_bull_regime and _rsi_oversold and higher_down:
            # 1h DOWN 신호의 실제 가중치를 0.05로 낮춰 score 재계산
            _adj_score = 0.0
            _adj_wt    = 0.0
            for _s in signals:
                _w = 0.05 if (_s.timeframe == '1h' and
                              _s.direction.value < 0) else _s.weight
                _adj_score += _s.direction.value * _s.strength * _w
                _adj_wt    += _w
            score = (_adj_score / (_adj_wt or 1)) + rsi_bonus
            score = score + (tf_bonus if score > 0 else -tf_bonus)
            higher_down = False  # soft-fail: 거부권 해제
            logger.info(
                f'[FX16-1] {"MTFMerger"} BULL+RSI과매도({avg_rsi:.0f}) '
                f'1h DOWN soft-fail → score재계산={score:.3f}'
            )
        allow_buy  = score > 0.2 and not higher_down and mid_agreement
        allow_sell = score < -0.2 and not higher_up

        if score > 1.2:
            final = TFDirection.STRONG_UP
        elif score > 0.2:
            final = TFDirection.UP
        elif score < -1.2:
            final = TFDirection.STRONG_DOWN
        elif score < -0.2:
            final = TFDirection.DOWN
        else:
            final = TFDirection.NEUTRAL

        dominant   = max(signals, key=lambda s: s.weight * abs(s.direction.value))
        tf_summary = "/".join(
            f"{s.timeframe}:{s.direction.name[:1]}" for s in signals
        )

        # mtf_aligned 계산 (상위/하위 TF 방향 일치 여부)
        lower_tfs     = [s for s in signals if s.timeframe in ("1h", "15m", "5m", "1m")]
        lower_up      = sum(
            1 for s in lower_tfs
            if s.direction in (TFDirection.UP, TFDirection.STRONG_UP)
        )
        lower_down    = sum(
            1 for s in lower_tfs
            if s.direction in (TFDirection.DOWN, TFDirection.STRONG_DOWN)
        )
        lower_bullish = lower_up   > lower_down if lower_tfs else False
        lower_bearish = lower_down > lower_up   if lower_tfs else False

        mtf_aligned = (
            (higher_up   and lower_bullish) or
            (higher_down and lower_bearish)
        )

        # [MTF-2 FIX] f-string 중첩 따옴표 제거 → 변수 분리
        # 이전: f"BUY={"'✅'" if ...}" → Python 3.11 이하 SyntaxError
        buy_icon  = "✅" if allow_buy  else "❌"
        sell_icon = "✅" if allow_sell else "❌"

        return MTFResult(
            combined_score  = score,
            final_direction = final,
            allow_buy       = allow_buy,
            allow_sell      = allow_sell,
            tf_signals      = signals,
            dominant_tf     = dominant.timeframe,
            mtf_aligned     = mtf_aligned,
            reason          = (
                f"MTF합산={score:.2f} | TF={tf_count}개({tf_summary}) | "
                f"RSI={avg_rsi:.0f} | 지배TF={dominant.timeframe} | "
                f"정렬={'✅' if mtf_aligned else '❌'} | "
                f"BUY={buy_icon} SELL={sell_icon}"
            ),
        )
