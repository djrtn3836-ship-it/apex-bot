"""
APEX BOT - 변동성 돌파 전략
래리 윌리엄스 변동성 돌파 전략 (업비트 최적화)
한국 비트코인 봇 가장 많이 쓰는 검증된 전략
"""
import pandas as pd
import numpy as np
from typing import Optional
from strategies.base_strategy import BaseStrategy, StrategySignal, SignalType


class VolatilityBreakoutStrategy(BaseStrategy):
    NAME = "volatility_breakout"
    DESCRIPTION = "래리 윌리엄스 변동성 돌파 (k=0.5)"
    WEIGHT = 2.0          # 검증된 전략, 높은 가중치
    MIN_CANDLES = 10

    def _default_params(self) -> dict:
        return {
            "k": 0.5,                    # 돌파 계수 (0.3~0.7, 최적값 0.5)
            "k_adaptive": True,          # 적응형 k 사용 여부
            "volume_confirm": True,      # 거래량 확인
            "volume_threshold": 1.2,     # 평균 거래량 대비 배수
            "profit_taking_ratio": 0.98, # 당일 고점 대비 수익 실현
        }

    def _calculate_adaptive_k(self, df: pd.DataFrame, window: int = 20) -> float:
        """
        적응형 k 값 계산
        변동성이 높은 구간에서는 k를 낮게, 낮은 구간에서는 높게
        """
        recent = df.tail(window)
        ranges = recent["high"] - recent["low"]
        closes = recent["close"]
        normalized_ranges = ranges / closes

        # 변동성 백분위
        current_vol = float(normalized_ranges.iloc[-1])
        avg_vol = float(normalized_ranges.mean())

        if avg_vol == 0:
            return self.params["k"]

        vol_ratio = current_vol / avg_vol
        # 변동성이 높으면 k 낮게 (0.3), 낮으면 k 높게 (0.7)
        adaptive_k = 0.5 * (2.0 - min(vol_ratio, 2.0)) * 0.5 + 0.25
        return round(float(np.clip(adaptive_k, 0.3, 0.7)), 2)

    def generate_signal(self, df: pd.DataFrame, market: str,
                        timeframe: str = "60") -> Optional[StrategySignal]:
        if not self.validate_df(df) or len(df) < 5:
            return None

        p = self.params
        curr = df.iloc[-1]
        prev = df.iloc[-2]

        curr_close = float(curr["close"])
        curr_high = float(curr["high"])
        curr_low = float(curr["low"])
        curr_volume = float(curr["volume"])

        # 전일 데이터 (변동폭 계산 기준)
        prev_high = float(prev.get("prev_high", prev["high"]))
        prev_low = float(prev.get("prev_low", prev["low"]))
        prev_close = float(prev["close"])

        # 변동폭
        daily_range = prev_high - prev_low

        if daily_range <= 0:
            return None

        # k 값 결정
        k = self._calculate_adaptive_k(df) if p["k_adaptive"] else p["k"]

        # 돌파 목표가
        breakout_target = prev_close + daily_range * k

        # ATR 기반 손절/목표
        atr_val = float(df.get("atr_14", pd.Series([daily_range * 0.5])).iloc[-1])

        # ─ 매수 조건: 현재가가 돌파 목표가 상회 ─
        if curr_close >= breakout_target:
            # 거래량 확인
            volume_ok = True
            if p["volume_confirm"] and "volume_sma_20" in df.columns:
                avg_volume = float(df["volume_sma_20"].iloc[-1])
                if avg_volume > 0:
                    volume_ok = curr_volume >= avg_volume * p["volume_threshold"]

            if not volume_ok:
                return None

            # 돌파 강도 계산
            breakout_strength = (curr_close - breakout_target) / breakout_target
            score = min(breakout_strength * 100, 1.0)
            confidence = 0.75

            # 거래량이 강하면 신뢰도 상향
            if "volume_ratio" in df.columns and float(df["volume_ratio"].iloc[-1]) > 2.0:
                confidence = 0.88

            # RSI 확인 (과매수 구간 회피)
            if "rsi_14" in df.columns:
                curr_rsi = float(df["rsi_14"].iloc[-1])
                if curr_rsi > 75:        # RSI 과매수 → 신호 약화
                    confidence *= 0.7
                elif curr_rsi < 60:      # RSI 정상 구간 → 신호 강화
                    confidence *= 1.1

            confidence = min(confidence, 0.95)

            return self._create_signal(
                SignalType.BUY, score, confidence, market,
                entry_price=breakout_target,    # 돌파 시점 가격으로 진입
                stop_loss=prev_low - atr_val * 0.5,
                take_profit=curr_close + daily_range * 1.5,
                reason=(
                    f"변동성 돌파 | k={k} | "
                    f"목표:{breakout_target:,.0f} | 현재:{curr_close:,.0f} | "
                    + (f"거래량비: {curr_volume / float(df['volume_sma_20'].iloc[-1]):.1f}x" if 'volume_sma_20' in df.columns else "거래량비: N/A")
                ),
                timeframe=timeframe,
                metadata={
                    "k": k,
                    "breakout_target": breakout_target,
                    "daily_range": daily_range,
                    "prev_high": prev_high,
                    "prev_low": prev_low,
                    "breakout_strength": breakout_strength,
                }
            )

        return None

# 하위 호환 alias
VolBreakoutStrategy = VolatilityBreakoutStrategy
