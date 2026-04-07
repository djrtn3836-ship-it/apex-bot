import pandas as pd
from strategies.base_strategy import BaseStrategy

class RSIDivergenceStrategy(BaseStrategy):
    NAME = "RSI_Divergence"
    def __init__(self):
        super().__init__()
        self.params = {"period": 14, "oversold": 30, "overbought": 70, "score": 1.0}

    def _rsi(self, close, period):
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(period).mean()
        loss  = (-delta.clip(upper=0)).rolling(period).mean()
        rs    = gain / (loss + 1e-9)
        return 100 - (100 / (1 + rs))

    async def analyze(self, market: str, df: pd.DataFrame, **kwargs) -> dict | None:
        if df is None or len(df) < 20:
            return None
        try:
            rsi = self._rsi(df["close"], self.params["period"])
            cur = rsi.iloc[-1]
            if cur < self.params["oversold"]:
                return {"signal": "BUY",  "score": self.params["score"],
                        "reason": f"RSI 과매도({cur:.1f})", "strategy": self.NAME}
            if cur > self.params["overbought"]:
                return {"signal": "SELL", "score": self.params["score"],
                        "reason": f"RSI 과매수({cur:.1f})", "strategy": self.NAME}
        except Exception:
            pass
        return None
