from typing import Optional
import pandas as pd
from strategies.base_strategy import BaseStrategy, StrategySignal, SignalType


class ATRChannelStrategy(BaseStrategy):
    NAME = "ATR_Channel"
    DESCRIPTION = "ATR 채널 이탈 전략"
    WEIGHT = 0.9
    MIN_CANDLES = 30

    def _default_params(self) -> dict:
        return {"atr_period": 14, "channel_mult": 1.5}

    def generate_signal(self, df: pd.DataFrame, market: str,
                        timeframe: str = "60") -> Optional[StrategySignal]:
        if df is None or len(df) < self.MIN_CANDLES:
            return None
        try:
            tr = pd.concat([
                df["high"] - df["low"],
                (df["high"] - df["close"].shift()).abs(),
                (df["low"]  - df["close"].shift()).abs()
            ], axis=1).max(axis=1)
            atr    = tr.rolling(self.params["atr_period"]).mean()
            mid    = df["close"].rolling(self.params["atr_period"]).mean()
            upper  = mid + self.params["channel_mult"] * atr
            lower  = mid - self.params["channel_mult"] * atr
            price  = float(df["close"].iloc[-1])
            atr_v  = float(atr.iloc[-1]) or price * 0.02

            if price < float(lower.iloc[-1]):
                return self._create_signal(
                    signal=SignalType.BUY, score=0.60, confidence=0.65,
                    market=market, entry_price=price,
                    stop_loss=price - atr_v * 1.5, take_profit=float(mid.iloc[-1]),
                    reason=f"ATR 채널 하단 이탈", timeframe=timeframe)
            if price > float(upper.iloc[-1]):
                return self._create_signal(
                    signal=SignalType.SELL, score=-0.60, confidence=0.65,
                    market=market, entry_price=price,
                    stop_loss=price + atr_v * 1.5, take_profit=float(mid.iloc[-1]),
                    reason=f"ATR 채널 상단 돌파", timeframe=timeframe)
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"atr_channel signal error: {e}")
        return None
