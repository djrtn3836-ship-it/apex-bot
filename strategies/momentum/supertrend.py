"""
APEX BOT - Supertrend Strategy
ATR-based trend following with direction reversal signals
"""
import pandas as pd
import numpy as np
from typing import Optional
from datetime import datetime
from loguru import logger

from strategies.base_strategy import BaseStrategy, StrategySignal, SignalType


class SupertrendStrategy(BaseStrategy):
    NAME = "Supertrend"
    DESCRIPTION = "ATR 기반 슈퍼트렌드 추세 추종"
    WEIGHT = 1.1
    MIN_CANDLES = 30

    def _default_params(self) -> dict:
        return {"atr_period": 10, "multiplier": 3.0, "min_adx": 25}

    def generate_signal(self, df: pd.DataFrame, market: str,
                        timeframe: str = "60") -> Optional[StrategySignal]:
        if df is None or len(df) < self.MIN_CANDLES:
            return None
        try:
            last = df.iloc[-1]
            prev = df.iloc[-2]

            current_dir = last.get("supertrend_dir", 0)
            prev_dir    = prev.get("supertrend_dir", 0)
            adx         = float(last.get("adx", 0) or 0)
            rsi         = float(last.get("rsi", 50) or 50)
            close       = float(last["close"])
            st_line     = float(last.get("supertrend", close) or close)
            atr         = float(last.get("atr", close * 0.02) or close * 0.02)
            vol_ratio   = float(last.get("vol_ratio", 1.0) or 1.0)

            min_adx = self.params.get("min_adx", 25)
            direction_changed = (current_dir != prev_dir) and (prev_dir != 0)

            if direction_changed and current_dir > 0 and adx > min_adx and vol_ratio > 1.2:
                conf = min(0.85, 0.65 + (adx - min_adx) * 0.005)
                return self._create_signal(
                    signal=SignalType.BUY,
                    score=0.80, confidence=conf,
                    market=market,
                    entry_price=close,
                    stop_loss=close - atr * 1.5,
                    take_profit=close + atr * 3.0,
                    reason=f"Supertrend UP | ADX={adx:.1f} | vol={vol_ratio:.1f}x",
                    timeframe=timeframe,
                    metadata={"adx": adx, "rsi": rsi, "st_line": st_line},
                )

            if direction_changed and current_dir < 0 and adx > min_adx:
                conf = min(0.85, 0.65 + (adx - min_adx) * 0.005)
                return self._create_signal(
                    signal=SignalType.SELL,
                    score=0.80, confidence=conf,
                    market=market,
                    entry_price=close,
                    stop_loss=close + atr * 1.5,
                    take_profit=close - atr * 3.0,
                    reason=f"Supertrend DOWN | ADX={adx:.1f}",
                    timeframe=timeframe,
                    metadata={"adx": adx, "rsi": rsi},
                )

            # Continuation signal
            if current_dir > 0 and adx > 35 and rsi < 65 and vol_ratio > 1.5:
                return self._create_signal(
                    signal=SignalType.BUY,
                    score=0.60, confidence=0.60,
                    market=market,
                    entry_price=close,
                    stop_loss=close - atr * 1.5,
                    take_profit=close + atr * 3.0,
                    reason=f"Supertrend continuation | ADX={adx:.1f}",
                    timeframe=timeframe,
                )

            return None
        except Exception as e:
            logger.error(f"{self.NAME} error ({market}): {e}")
            return None
