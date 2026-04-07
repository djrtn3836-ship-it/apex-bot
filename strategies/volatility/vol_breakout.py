import pandas as pd
from strategies.base_strategy import BaseStrategy

class VolBreakoutStrategy(BaseStrategy):
    NAME = "Vol_Breakout"
    def __init__(self):
        super().__init__()
        self.params = {"lookback": 20, "vol_mult": 2.5, "score": 1.4}

    async def analyze(self, market: str, df: pd.DataFrame, **kwargs) -> dict | None:
        if df is None or len(df) < 25:
            return None
        try:
            close    = df["close"]
            vol      = df.get("volume", pd.Series([1]*len(df), index=df.index))
            avg_vol  = vol.rolling(self.params["lookback"]).mean().iloc[-1]
            cur_vol  = vol.iloc[-1]
            ret      = close.pct_change().iloc[-1]

            if cur_vol > avg_vol * self.params["vol_mult"]:
                if ret > 0.01:
                    return {"signal": "BUY",  "score": self.params["score"],
                            "reason": f"거래량 급증 상승({ret*100:.1f}%)", "strategy": self.NAME}
                if ret < -0.01:
                    return {"signal": "SELL", "score": self.params["score"],
                            "reason": f"거래량 급증 하락({ret*100:.1f}%)", "strategy": self.NAME}
        except Exception:
            pass
        return None
