import pandas as pd
from strategies.base_strategy import BaseStrategy

class OrderBlockStrategy(BaseStrategy):
    NAME = "Order_Block"
    def __init__(self):
        super().__init__()
        self.params = {"lookback": 10, "body_ratio": 0.6, "score": 1.2}

    async def analyze(self, market: str, df: pd.DataFrame, **kwargs) -> dict | None:
        if df is None or len(df) < 15:
            return None
        try:
            lb    = self.params["lookback"]
            close = df["close"]
            open_ = df["open"]
            high  = df["high"]
            low   = df["low"]

            body       = (close - open_).abs()
            full_range = (high - low) + 1e-9
            body_ratio = body / full_range

            # 최근 N봉에서 강한 상승 캔들 (불리시 오더블록)
            recent = df.tail(lb)
            bull_ob = recent[(recent["close"] > recent["open"]) &
                             (body_ratio.tail(lb) > self.params["body_ratio"])]
            bear_ob = recent[(recent["close"] < recent["open"]) &
                             (body_ratio.tail(lb) > self.params["body_ratio"])]

            cur_price = close.iloc[-1]

            if not bull_ob.empty:
                ob_low = bull_ob["low"].iloc[-1]
                if abs(cur_price - ob_low) / ob_low < 0.005:
                    return {"signal": "BUY",  "score": self.params["score"],
                            "reason": "불리시 오더블록 지지", "strategy": self.NAME}
            if not bear_ob.empty:
                ob_high = bear_ob["high"].iloc[-1]
                if abs(cur_price - ob_high) / ob_high < 0.005:
                    return {"signal": "SELL", "score": self.params["score"],
                            "reason": "베어리시 오더블록 저항", "strategy": self.NAME}
        except Exception:
            pass
        return None
