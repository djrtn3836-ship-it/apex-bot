from typing import Optional
import pandas as pd
from strategies.base_strategy import BaseStrategy, StrategySignal, SignalType


class RSIDivergenceStrategy(BaseStrategy):
    NAME = "RSI_Divergence"
    DESCRIPTION = "RSI 과매수/과매도 역발상 전략"
    WEIGHT = 1.0
    MIN_CANDLES = 30

    def _default_params(self) -> dict:
        return {"period": 14, "oversold": 30, "overbought": 70}

    def _rsi(self, close: pd.Series, period: int) -> pd.Series:
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(period).mean()
        loss  = (-delta.clip(upper=0)).rolling(period).mean()
        rs    = gain / (loss + 1e-9)
        return 100 - (100 / (1 + rs))

    def generate_signal(self, df: pd.DataFrame, market: str,
                        timeframe: str = "60") -> Optional[StrategySignal]:
        if df is None or len(df) < self.MIN_CANDLES:
            return None
        try:
            rsi   = self._rsi(df["close"], self.params["period"])
            cur   = float(rsi.iloc[-1])
            price = float(df["close"].iloc[-1])
            atr   = float(pd.concat([df["high"]-df["low"],(df["high"]-df["close"].shift()).abs(),(df["low"]-df["close"].shift()).abs()],axis=1).max(axis=1).rolling(14).mean().iloc[-1]) or price * 0.02

            if cur < self.params["oversold"]:
                # ✅ 동적 score: RSI가 낮을수록(극단적일수록) 높은 score
                depth = (self.params["oversold"] - cur) / self.params["oversold"]
                score = round(min(0.55 + depth * 0.40, 0.95), 3)
                conf  = min(0.50 + depth * 0.45, 0.92)
                return self._create_signal(
                    signal=SignalType.BUY, score=score, confidence=conf,
                    market=market, entry_price=price,
                    stop_loss=price - atr * 1.5, take_profit=price + atr * 3.0,
                    reason=f"RSI 과매도({cur:.1f})", timeframe=timeframe)
            if cur > self.params["overbought"]:
                # ✅ 동적 score: RSI가 높을수록(극단적일수록) 높은 score
                depth = (cur - self.params["overbought"]) / (100 - self.params["overbought"])
                score = round(min(0.55 + depth * 0.40, 0.95), 3)
                conf  = min(0.50 + depth * 0.45, 0.92)
                return self._create_signal(
                    signal=SignalType.SELL, score=-score, confidence=conf,
                    market=market, entry_price=price,
                    stop_loss=price + atr * 1.5, take_profit=price - atr * 3.0,
                    reason=f"RSI 과매수({cur:.1f})", timeframe=timeframe)
        except Exception:
            pass
        return None
