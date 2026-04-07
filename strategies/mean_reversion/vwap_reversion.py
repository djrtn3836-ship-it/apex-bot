import pandas as pd
from strategies.base_strategy import BaseStrategy

class VWAPReversionStrategy(BaseStrategy):
    NAME = "VWAP_Reversion"
    def __init__(self):
        super().__init__()
        self.params = {"dev_threshold": 0.02, "score": 1.2}

    async def analyze(self, market: str, df: pd.DataFrame, **kwargs) -> dict | None:
        if df is None or len(df) < 10:
            return None
        try:
            tp   = (df["high"] + df["low"] + df["close"]) / 3
            vol  = df.get("volume", pd.Series([1]*len(df), index=df.index))
            vwap = (tp * vol).cumsum() / (vol.cumsum() + 1e-9)
            dev  = (df["close"].iloc[-1] - vwap.iloc[-1]) / (vwap.iloc[-1] + 1e-9)

            if dev < -self.params["dev_threshold"]:
                return {"signal": "BUY",  "score": self.params["score"],
                        "reason": f"VWAP 하방 이탈({dev*100:.1f}%)", "strategy": self.NAME}
            if dev > self.params["dev_threshold"]:
                return {"signal": "SELL", "score": self.params["score"],
                        "reason": f"VWAP 상방 이탈({dev*100:.1f}%)", "strategy": self.NAME}
        except Exception:
            pass
        return None
