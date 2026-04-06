"""
APEX BOT - 볼린저 밴드 스퀴즈 전략
BB + Keltner Channel 스퀴즈 후 방향성 돌파 포착
"""
import pandas as pd
import numpy as np
from typing import Optional
from strategies.base_strategy import BaseStrategy, StrategySignal, SignalType


class BollingerSqueezeStrategy(BaseStrategy):
    NAME = "bollinger_squeeze"
    DESCRIPTION = "볼린저 밴드 스퀴즈 + 방향성 돌파"
    WEIGHT = 1.0
    MIN_CANDLES = 60

    def _default_params(self) -> dict:
        return {
            "bb_period": 20,
            "bb_std": 2.0,
            "kc_period": 20,
            "kc_atr_period": 20,
            "kc_mult": 1.5,
            "min_squeeze_candles": 5,     # 최소 스퀴즈 지속 캔들
            "bb_pct_threshold": 0.1,      # 밴드 내 위치 임계값
        }

    def generate_signal(self, df: pd.DataFrame, market: str,
                        timeframe: str = "60") -> Optional[StrategySignal]:
        if not self.validate_df(df):
            return None

        if not all(c in df.columns for c in ["bb_upper", "bb_lower", "kc_upper", "kc_lower", "squeeze_on", "squeeze_mom"]):
            return None

        close = df["close"]
        bb_upper = df["bb_upper"]
        bb_lower = df["bb_lower"]
        bb_mid = df["bb_mid"]
        squeeze_on = df["squeeze_on"]
        squeeze_mom = df["squeeze_mom"]
        atr = df.get("atr_14", (df["high"] - df["low"]).rolling(14).mean())

        curr_close = float(close.iloc[-1])
        curr_atr = float(atr.iloc[-1])
        curr_mom = float(squeeze_mom.iloc[-1])
        prev_mom = float(squeeze_mom.iloc[-2])
        prev_squeeze = bool(squeeze_on.iloc[-3])  # 3봉 전에 스퀴즈 있었는지

        # 스퀴즈 해제 감지 (스퀴즈 중 → 해제)
        was_squeezing = squeeze_on.iloc[-5:-1].any()
        is_releasing = not squeeze_on.iloc[-1] and was_squeezing

        curr_bb_pct = float(df["bb_pct"].iloc[-1]) if "bb_pct" in df.columns else 0.5

        # ─ 매수 조건: 스퀴즈 해제 + 모멘텀 상향 ─
        if is_releasing and curr_mom > 0 and curr_mom > prev_mom:
            # BB 하단 부근에서 돌파 (더 강한 신호)
            confidence = 0.70
            if curr_bb_pct < 0.3:   # 밴드 하단 30% 이내
                confidence = 0.85

            score = min(abs(curr_mom / curr_close) * 500, 1.0)
            return self._create_signal(
                SignalType.BUY, score, confidence, market,
                curr_close,
                curr_close - curr_atr * 1.5,
                curr_close + curr_atr * 3.0,
                f"BB 스퀴즈 해제 상향 돌파 | 모멘텀: {curr_mom:.4f}",
                timeframe,
                {"squeeze_mom": curr_mom, "bb_pct": curr_bb_pct, "squeeze_released": True}
            )

        # ─ BB 하단 터치 후 반등 (스퀴즈 없어도) ─
        if curr_bb_pct <= 0.05 and curr_mom > prev_mom:
            return self._create_signal(
                SignalType.BUY, 0.7, 0.65, market,
                curr_close,
                float(bb_lower.iloc[-1]) - curr_atr * 0.5,
                float(bb_mid.iloc[-1]),
                f"BB 하단 터치 반등 | BB%: {curr_bb_pct:.2f}",
                timeframe,
                {"bb_pct": curr_bb_pct, "bb_lower": float(bb_lower.iloc[-1])}
            )

        # ─ 매도 조건: 스퀴즈 해제 + 모멘텀 하향 ─
        if is_releasing and curr_mom < 0 and curr_mom < prev_mom:
            confidence = 0.70
            if curr_bb_pct > 0.7:
                confidence = 0.85

            score = -min(abs(curr_mom / curr_close) * 500, 1.0)
            return self._create_signal(
                SignalType.SELL, score, confidence, market,
                curr_close,
                curr_close + curr_atr * 1.5,
                curr_close - curr_atr * 3.0,
                f"BB 스퀴즈 해제 하향 돌파 | 모멘텀: {curr_mom:.4f}",
                timeframe,
                {"squeeze_mom": curr_mom, "bb_pct": curr_bb_pct}
            )

        # ─ BB 상단 터치 (과매수 경고) ─
        if curr_bb_pct >= 0.95 and curr_mom < prev_mom:
            return self._create_signal(
                SignalType.SELL, -0.6, 0.60, market,
                curr_close,
                float(bb_upper.iloc[-1]) + curr_atr * 0.5,
                float(bb_mid.iloc[-1]),
                f"BB 상단 터치 반락 | BB%: {curr_bb_pct:.2f}",
                timeframe,
                {"bb_pct": curr_bb_pct}
            )

        return None
