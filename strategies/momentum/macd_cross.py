from datetime import datetime
from typing import Optional
import pandas as pd
from strategies.base_strategy import BaseStrategy, StrategySignal, SignalType


class MACDCrossStrategy(BaseStrategy):
    NAME = "MACD_Cross"
    DESCRIPTION = "MACD 골든/데드 크로스 전략"
    WEIGHT = 1.5
    MIN_CANDLES = 50

    def _default_params(self) -> dict:
        return {"fast": 12, "slow": 26, "signal": 9}

    def generate_signal(self, df: pd.DataFrame, market: str,
                        timeframe: str = "60") -> Optional[StrategySignal]:
        if df is None or len(df) < self.MIN_CANDLES:
            return None
        try:
            close = df["close"]
            exp1  = close.ewm(span=self.params["fast"],   adjust=False).mean()
            exp2  = close.ewm(span=self.params["slow"],   adjust=False).mean()
            macd  = exp1 - exp2
            sig   = macd.ewm(span=self.params["signal"],  adjust=False).mean()
            hist  = macd - sig
            price = float(close.iloc[-1])
            atr   = float(df["high"].iloc[-14:].mean() - df["low"].iloc[-14:].mean()) or price * 0.02

            if hist.iloc[-1] > 0 and hist.iloc[-2] <= 0:
                return self._create_signal(
                    signal=SignalType.BUY, score=0.7, confidence=0.65,
                    market=market, entry_price=price,
                    stop_loss=price - atr * 1.5, take_profit=price + atr * 3.0,
                    reason="MACD 골든크로스", timeframe=timeframe)
            if hist.iloc[-1] < 0 and hist.iloc[-2] >= 0:
                return self._create_signal(
                    signal=SignalType.SELL, score=-0.7, confidence=0.65,
                    market=market, entry_price=price,
                    stop_loss=price + atr * 1.5, take_profit=price - atr * 3.0,
                    reason="MACD 데드크로스", timeframe=timeframe)
        except Exception as e:
            pass
        return None
