from typing import Optional
import pandas as pd
from strategies.base_strategy import BaseStrategy, StrategySignal, SignalType


class SupertrendStrategy(BaseStrategy):
    NAME = "Supertrend"
    DESCRIPTION = "슈퍼트렌드 추세 추종 전략"
    WEIGHT = 1.1
    MIN_CANDLES = 30

    def _default_params(self) -> dict:
        return {"period": 10, "multiplier": 3.0}

    def generate_signal(self, df: pd.DataFrame, market: str,
                        timeframe: str = "60") -> Optional[StrategySignal]:
        if df is None or len(df) < self.MIN_CANDLES:
            return None
        try:
            hl2 = (df["high"] + df["low"]) / 2
            tr  = pd.concat([
                df["high"] - df["low"],
                (df["high"] - df["close"].shift()).abs(),
                (df["low"]  - df["close"].shift()).abs()
            ], axis=1).max(axis=1)
            atr    = tr.rolling(self.params["period"]).mean()
            m      = self.params["multiplier"]
            upper  = hl2 + m * atr
            lower  = hl2 - m * atr
            close  = df["close"]
            price  = float(close.iloc[-1])
            atr_v  = float(atr.iloc[-1]) or price * 0.02

            trend = [0] * len(df)
            for i in range(1, len(df)):
                if float(close.iloc[i]) > float(upper.iloc[i-1]):
                    trend[i] = 1
                elif float(close.iloc[i]) < float(lower.iloc[i-1]):
                    trend[i] = -1
                else:
                    trend[i] = trend[i-1]

            if trend[-1] == 1 and trend[-2] != 1:
                return self._create_signal(
                    signal=SignalType.BUY, score=0.65, confidence=0.70,
                    market=market, entry_price=price,
                    stop_loss=price - atr_v * 1.5, take_profit=price + atr_v * 3.0,
                    reason="Supertrend 상향 전환", timeframe=timeframe)
            if trend[-1] == -1 and trend[-2] != -1:
                return self._create_signal(
                    signal=SignalType.SELL, score=-0.65, confidence=0.70,
                    market=market, entry_price=price,
                    stop_loss=price + atr_v * 1.5, take_profit=price - atr_v * 3.0,
                    reason="Supertrend 하향 전환", timeframe=timeframe)
        except Exception:
            pass
        return None
