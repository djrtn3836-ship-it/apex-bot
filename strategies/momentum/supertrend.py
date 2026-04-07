import pandas as pd
import numpy as np
from strategies.base_strategy import BaseStrategy

class SupertrendStrategy(BaseStrategy):
    NAME = "Supertrend"
    def __init__(self):
        super().__init__()
        self.params = {"period": 10, "multiplier": 3.0, "score": 1.1}

    async def analyze(self, market: str, df: pd.DataFrame, **kwargs) -> dict | None:
        if df is None or len(df) < 15:
            return None
        try:
            hl2   = (df["high"] + df["low"]) / 2
            tr    = pd.concat([
                df["high"] - df["low"],
                (df["high"] - df["close"].shift()).abs(),
                (df["low"]  - df["close"].shift()).abs()
            ], axis=1).max(axis=1)
            atr   = tr.rolling(self.params["period"]).mean()
            m     = self.params["multiplier"]
            upper = hl2 + m * atr
            lower = hl2 - m * atr

            close = df["close"]
            trend = pd.Series(index=df.index, dtype=float)
            for i in range(1, len(df)):
                if close.iloc[i] > upper.iloc[i-1]:
                    trend.iloc[i] = 1
                elif close.iloc[i] < lower.iloc[i-1]:
                    trend.iloc[i] = -1
                else:
                    trend.iloc[i] = trend.iloc[i-1] if i > 1 else 0

            if trend.iloc[-1] == 1 and trend.iloc[-2] != 1:
                return {"signal": "BUY",  "score": self.params["score"],
                        "reason": "Supertrend 상향 전환", "strategy": self.NAME}
            if trend.iloc[-1] == -1 and trend.iloc[-2] != -1:
                return {"signal": "SELL", "score": self.params["score"],
                        "reason": "Supertrend 하향 전환", "strategy": self.NAME}
        except Exception:
            pass
        return None
