from typing import Optional
import pandas as pd
from strategies.base_strategy import BaseStrategy, StrategySignal, SignalType


class BollingerSqueezeStrategy(BaseStrategy):
    NAME = "Bollinger_Squeeze"
    DESCRIPTION = "볼린저밴드 스퀴즈 돌파 전략"
    WEIGHT = 1.3
    MIN_CANDLES = 30

    def _default_params(self) -> dict:
        return {"period": 20, "std_dev": 2.0, "squeeze_pct": 0.04}

    def generate_signal(self, df: pd.DataFrame, market: str,
                        timeframe: str = "60") -> Optional[StrategySignal]:
        if df is None or len(df) < self.MIN_CANDLES:
            return None
        try:
            close  = df["close"]
            mid    = close.rolling(self.params["period"]).mean()
            std    = close.rolling(self.params["period"]).std()
            upper  = mid + self.params["std_dev"] * std
            lower  = mid - self.params["std_dev"] * std
            bb_pct = (close - lower) / (upper - lower + 1e-9)
            width  = (upper - lower) / (mid + 1e-9)
            price  = float(close.iloc[-1])
            atr    = float(df["high"].iloc[-14:].mean() - df["low"].iloc[-14:].mean()) or price * 0.02

            squeezed = float(width.iloc[-1]) < self.params["squeeze_pct"]
            if squeezed and float(bb_pct.iloc[-1]) < 0.2:
                return self._create_signal(
                    signal=SignalType.BUY, score=0.75, confidence=0.72,
                    market=market, entry_price=price,
                    stop_loss=price - atr * 1.5, take_profit=price + atr * 3.0,
                    reason=f"BB 스퀴즈 하단({float(bb_pct.iloc[-1]):.2f})", timeframe=timeframe)
            if squeezed and float(bb_pct.iloc[-1]) > 0.8:
                return self._create_signal(
                    signal=SignalType.SELL, score=-0.75, confidence=0.72,
                    market=market, entry_price=price,
                    stop_loss=price + atr * 1.5, take_profit=price - atr * 3.0,
                    reason=f"BB 스퀴즈 상단({float(bb_pct.iloc[-1]):.2f})", timeframe=timeframe)
        except Exception:
            pass
        return None
