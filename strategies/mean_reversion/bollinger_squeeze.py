import pandas as pd
from strategies.base_strategy import BaseStrategy

class BollingerSqueezeStrategy(BaseStrategy):
    NAME = "Bollinger_Squeeze"
    def __init__(self):
        super().__init__()
        self.params = {"period": 20, "std": 2.0, "squeeze_pct": 0.03, "score": 1.3}

    async def analyze(self, market: str, df: pd.DataFrame, **kwargs) -> dict | None:
        if df is None or len(df) < 25:
            return None
        try:
            close  = df["close"]
            mid    = close.rolling(self.params["period"]).mean()
            std    = close.rolling(self.params["period"]).std()
            upper  = mid + self.params["std"] * std
            lower  = mid - self.params["std"] * std
            bb_pct = (close - lower) / (upper - lower + 1e-9)
            width  = (upper - lower) / (mid + 1e-9)

            squeezed = width.iloc[-1] < self.params["squeeze_pct"]
            if squeezed and bb_pct.iloc[-1] > 0.8:
                return {"signal": "SELL", "score": self.params["score"],
                        "reason": "BB 스퀴즈 상단 돌파", "strategy": self.NAME}
            if squeezed and bb_pct.iloc[-1] < 0.2:
                return {"signal": "BUY",  "score": self.params["score"],
                        "reason": "BB 스퀴즈 하단 이탈", "strategy": self.NAME}
        except Exception:
            pass
        return None
