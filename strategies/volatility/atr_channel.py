import pandas as pd
from strategies.base_strategy import BaseStrategy

class ATRChannelStrategy(BaseStrategy):
    NAME = "ATR_Channel"
    def __init__(self):
        super().__init__()
        self.params = {"atr_period": 14, "channel_mult": 1.5, "score": 0.9}

    async def analyze(self, market: str, df: pd.DataFrame, **kwargs) -> dict | None:
        if df is None or len(df) < 20:
            return None
        try:
            tr  = pd.concat([
                df["high"] - df["low"],
                (df["high"] - df["close"].shift()).abs(),
                (df["low"]  - df["close"].shift()).abs()
            ], axis=1).max(axis=1)
            atr    = tr.rolling(self.params["atr_period"]).mean()
            mid    = df["close"].rolling(self.params["atr_period"]).mean()
            upper  = mid + self.params["channel_mult"] * atr
            lower  = mid - self.params["channel_mult"] * atr
            close  = df["close"].iloc[-1]

            if close < lower.iloc[-1]:
                return {"signal": "BUY",  "score": self.params["score"],
                        "reason": "ATR 채널 하단 이탈", "strategy": self.NAME}
            if close > upper.iloc[-1]:
                return {"signal": "SELL", "score": self.params["score"],
                        "reason": "ATR 채널 상단 돌파", "strategy": self.NAME}
        except Exception:
            pass
        return None
