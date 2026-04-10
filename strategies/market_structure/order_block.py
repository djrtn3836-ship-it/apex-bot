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
            atr    = float(pd.concat([df["high"]-df["low"],(df["high"]-df["close"].shift()).abs(),(df["low"]-df["close"].shift()).abs()],axis=1).max(axis=1).rolling(14).mean().iloc[-1]) or price * 0.02

            bull_ob = recent[(recent["close"] > recent["open"]) &
                             (ratio > self.params["body_ratio"])]
            bear_ob = recent[(recent["close"] < recent["open"]) &
                             (ratio > self.params["body_ratio"])]

            if not bull_ob.empty:
                ob_low      = float(bull_ob["low"].iloc[-1])
                touch_dist  = abs(price - ob_low) / (ob_low + 1e-9)
                if touch_dist < self.params["touch_pct"]:
                    # ✅ 동적 score: 바디 비율과 터치 근접도 반영
                    body_str = float(ratio[bull_ob.index[-1]])
                    prox     = 1.0 - (touch_dist / self.params["touch_pct"])
                    score    = round(min(0.55 + body_str * 0.25 + prox * 0.15, 0.95), 3)
                    conf     = round(min(0.60 + body_str * 0.20 + prox * 0.12, 0.92), 3)
                    return self._create_signal(
                        signal=SignalType.BUY, score=score, confidence=conf,
                        market=market, entry_price=price,
                        stop_loss=ob_low - atr, take_profit=price + atr * 3.0,
                        reason=f"불리시 오더블록 지지(바디={body_str:.2f})", timeframe=timeframe)
            if not bear_ob.empty:
                ob_high     = float(bear_ob["high"].iloc[-1])
                touch_dist  = abs(price - ob_high) / (ob_high + 1e-9)
                if touch_dist < self.params["touch_pct"]:
                    body_str = float(ratio[bear_ob.index[-1]])
                    prox     = 1.0 - (touch_dist / self.params["touch_pct"])
                    score    = round(min(0.55 + body_str * 0.25 + prox * 0.15, 0.95), 3)
                    conf     = round(min(0.60 + body_str * 0.20 + prox * 0.12, 0.92), 3)
                    return self._create_signal(
                        signal=SignalType.SELL, score=-score, confidence=conf,
                        market=market, entry_price=price,
                        stop_loss=ob_high + atr, take_profit=price - atr * 3.0,
                        reason=f"베어리시 오더블록 저항(바디={body_str:.2f})", timeframe=timeframe)
        except Exception:
            pass
        return None
