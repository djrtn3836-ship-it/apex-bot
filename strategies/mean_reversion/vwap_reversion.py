from typing import Optional
import pandas as pd
from strategies.base_strategy import BaseStrategy, StrategySignal, SignalType


class VWAPReversionStrategy(BaseStrategy):
    NAME = "VWAP_Reversion"
    DESCRIPTION = "VWAP 평균회귀 전략"
    WEIGHT = 1.2
    MIN_CANDLES = 30

    def _default_params(self) -> dict:
        return {"dev_threshold": 0.025}

    def generate_signal(self, df: pd.DataFrame, market: str,
                        timeframe: str = "60") -> Optional[StrategySignal]:
        if df is None or len(df) < self.MIN_CANDLES:
            return None
        try:
            tp   = (df["high"] + df["low"] + df["close"]) / 3
            vol  = df["volume"] if "volume" in df.columns else pd.Series(
                [1.0]*len(df), index=df.index)
            vwap = (tp * vol).cumsum() / (vol.cumsum() + 1e-9)
            price = float(df["close"].iloc[-1])
            vwap_v = float(vwap.iloc[-1])
            dev   = (price - vwap_v) / (vwap_v + 1e-9)
            atr   = float(df["high"].iloc[-14:].mean() - df["low"].iloc[-14:].mean()) or price * 0.02

            if dev < -self.params["dev_threshold"]:
                conf = min(0.9, 0.55 + abs(dev) * 5)
                return self._create_signal(
                    signal=SignalType.BUY, score=0.65, confidence=conf,
                    market=market, entry_price=price,
                    stop_loss=price - atr * 1.5, take_profit=vwap_v,
                    reason=f"VWAP 하방 이탈({dev*100:.1f}%)", timeframe=timeframe)
            if dev > self.params["dev_threshold"]:
                conf = min(0.9, 0.55 + abs(dev) * 5)
                return self._create_signal(
                    signal=SignalType.SELL, score=-0.65, confidence=conf,
                    market=market, entry_price=price,
                    stop_loss=price + atr * 1.5, take_profit=vwap_v,
                    reason=f"VWAP 상방 이탈({dev*100:.1f}%)", timeframe=timeframe)
        except Exception:
            pass
        return None
