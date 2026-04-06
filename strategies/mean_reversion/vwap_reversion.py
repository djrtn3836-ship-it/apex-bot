"""
APEX BOT - VWAP Reversion Strategy
VWAP deviation mean-reversion signals
"""
import pandas as pd
import numpy as np
from typing import Optional
from loguru import logger

from strategies.base_strategy import BaseStrategy, StrategySignal, SignalType


class VWAPReversionStrategy(BaseStrategy):
    NAME = "VWAP_Reversion"
    DESCRIPTION = "VWAP 이격 평균회귀 전략"
    WEIGHT = 1.2
    MIN_CANDLES = 50

    def _default_params(self) -> dict:
        return {
            "vwap_dev_buy": -0.015,   # VWAP 대비 -1.5% 이하 → 매수
            "vwap_dev_sell": 0.015,   # VWAP 대비 +1.5% 이상 → 매도
            "rsi_oversold": 35,
            "rsi_overbought": 65,
            "min_vol_ratio": 1.0,
        }

    def generate_signal(self, df: pd.DataFrame, market: str,
                        timeframe: str = "60") -> Optional[StrategySignal]:
        if df is None or len(df) < self.MIN_CANDLES:
            return None
        try:
            last = df.iloc[-1]
            close     = float(last["close"])
            vwap      = float(last.get("vwap", close) or close)
            rsi       = float(last.get("rsi", 50) or 50)
            bb_pct    = float(last.get("bb_pct", 0.5) or 0.5)
            atr       = float(last.get("atr", close * 0.02) or close * 0.02)
            vol_ratio = float(last.get("vol_ratio", 1.0) or 1.0)
            ema20     = float(last.get("ema20", close) or close)

            if vwap == 0:
                return None

            vwap_dev = (close - vwap) / vwap
            p = self.params

            # ── 매수: VWAP 아래 + RSI 과매도 + BB 하단 근처
            if (vwap_dev < p["vwap_dev_buy"]
                    and rsi < p["rsi_oversold"]
                    and bb_pct < 0.25
                    and close > ema20 * 0.97):   # 완전 붕괴 제외
                score = min(0.85, 0.60 + abs(vwap_dev) * 10)
                conf  = min(0.80, 0.55 + (p["rsi_oversold"] - rsi) * 0.01)
                return self._create_signal(
                    signal=SignalType.BUY, score=score, confidence=conf,
                    market=market, entry_price=close,
                    stop_loss=close - atr * 1.5,
                    take_profit=vwap,              # 목표: VWAP 회귀
                    reason=f"VWAP 하방 이탈 {vwap_dev:.2%} | RSI={rsi:.1f}",
                    timeframe=timeframe,
                    metadata={"vwap_dev": vwap_dev, "rsi": rsi, "bb_pct": bb_pct},
                )

            # ── 매도: VWAP 위 + RSI 과매수 + BB 상단 근처
            if (vwap_dev > p["vwap_dev_sell"]
                    and rsi > p["rsi_overbought"]
                    and bb_pct > 0.75):
                score = min(0.85, 0.60 + vwap_dev * 10)
                conf  = min(0.80, 0.55 + (rsi - p["rsi_overbought"]) * 0.01)
                return self._create_signal(
                    signal=SignalType.SELL, score=score, confidence=conf,
                    market=market, entry_price=close,
                    stop_loss=close + atr * 1.5,
                    take_profit=vwap,
                    reason=f"VWAP 상방 이탈 {vwap_dev:.2%} | RSI={rsi:.1f}",
                    timeframe=timeframe,
                    metadata={"vwap_dev": vwap_dev, "rsi": rsi, "bb_pct": bb_pct},
                )

            return None
        except Exception as e:
            logger.error(f"{self.NAME} error ({market}): {e}")
            return None
