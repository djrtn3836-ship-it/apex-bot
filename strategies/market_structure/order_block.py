from typing import Optional
import pandas as pd
from strategies.base_strategy import BaseStrategy, StrategySignal, SignalType


class OrderBlockStrategy(BaseStrategy):
    NAME = "Order_Block"
    DESCRIPTION = "스마트머니 오더블록 전략"
    WEIGHT = 1.2
    MIN_CANDLES = 30

    def _default_params(self) -> dict:
        return {"lookback": 10, "body_ratio": 0.6, "touch_pct": 0.005}

    def generate_signal(self, df: pd.DataFrame, market: str,
                        timeframe: str = "60") -> Optional[StrategySignal]:
        if df is None or len(df) < self.MIN_CANDLES:
            return None
        try:
            lb     = self.params["lookback"]
            recent = df.tail(lb)
            body   = (recent["close"] - recent["open"]).abs()
            rng    = (recent["high"] - recent["low"]) + 1e-9
            ratio  = body / rng
            price  = float(df["close"].iloc[-1])
            atr    = float((df["high"].iloc[-14:].mean() - df["low"].iloc[-14:].mean())) or price * 0.02

            bull_ob = recent[(recent["close"] > recent["open"]) &
                             (ratio > self.params["body_ratio"])]
            bear_ob = recent[(recent["close"] < recent["open"]) &
                             (ratio > self.params["body_ratio"])]

            if not bull_ob.empty:
                ob_low = float(bull_ob["low"].iloc[-1])
                if abs(price - ob_low) / ob_low < self.params["touch_pct"]:
                    return self._create_signal(
                        signal=SignalType.BUY, score=0.70, confidence=0.68,
                        market=market, entry_price=price,
                        stop_loss=ob_low - atr, take_profit=price + atr * 3.0,
                        reason="불리시 오더블록 지지", timeframe=timeframe)
            if not bear_ob.empty:
                ob_high = float(bear_ob["high"].iloc[-1])
                if abs(price - ob_high) / ob_high < self.params["touch_pct"]:
                    return self._create_signal(
                        signal=SignalType.SELL, score=-0.70, confidence=0.68,
                        market=market, entry_price=price,
                        stop_loss=ob_high + atr, take_profit=price - atr * 3.0,
                        reason="베어리시 오더블록 저항", timeframe=timeframe)
        except Exception:
            pass
        return None
