import pandas as pd
from strategies.base_strategy import BaseStrategy

class MACDCrossStrategy(BaseStrategy):
    NAME = "MACD_Cross"
    def __init__(self):
        super().__init__()
        self.params = {"fast": 12, "slow": 26, "signal": 9, "score": 1.5}

    async def analyze(self, market: str, df: pd.DataFrame, **kwargs) -> dict | None:
        if df is None or len(df) < 35:
            return None
        try:
            close = df["close"]
            exp1  = close.ewm(span=self.params["fast"],   adjust=False).mean()
            exp2  = close.ewm(span=self.params["slow"],   adjust=False).mean()
            macd  = exp1 - exp2
            sig   = macd.ewm(span=self.params["signal"],  adjust=False).mean()
            hist  = macd - sig

            if hist.iloc[-1] > 0 and hist.iloc[-2] <= 0:
                return {"signal": "BUY",  "score": self.params["score"],
                        "reason": "MACD 골든크로스", "strategy": self.NAME}
            if hist.iloc[-1] < 0 and hist.iloc[-2] >= 0:
                return {"signal": "SELL", "score": self.params["score"],
                        "reason": "MACD 데드크로스",  "strategy": self.NAME}
        except Exception:
            pass
        return None
