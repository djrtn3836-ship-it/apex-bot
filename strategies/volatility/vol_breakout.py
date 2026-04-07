from typing import Optional
import pandas as pd
from strategies.base_strategy import BaseStrategy, StrategySignal, SignalType


class VolBreakoutStrategy(BaseStrategy):
    NAME = "Vol_Breakout"
    DESCRIPTION = "거래량 급증 돌파 전략"
    WEIGHT = 1.4
    MIN_CANDLES = 30

    def _default_params(self) -> dict:
        return {"lookback": 20, "vol_mult": 2.5, "min_ret": 0.01}

    def generate_signal(self, df: pd.DataFrame, market: str,
                        timeframe: str = "60") -> Optional[StrategySignal]:
        if df is None or len(df) < self.MIN_CANDLES:
            return None
        try:
            close    = df["close"]
            vol      = df["volume"] if "volume" in df.columns else pd.Series(
                [1.0]*len(df), index=df.index)
            avg_vol  = vol.rolling(self.params["lookback"]).mean().iloc[-1]
            cur_vol  = float(vol.iloc[-1])
            ret      = float(close.pct_change().iloc[-1])
            price    = float(close.iloc[-1])
            atr      = float(df["high"].iloc[-14:].mean() - df["low"].iloc[-14:].mean()) or price * 0.02

            if cur_vol > avg_vol * self.params["vol_mult"]:
                if ret > self.params["min_ret"]:
                    return self._create_signal(
                        signal=SignalType.BUY, score=0.80, confidence=0.75,
                        market=market, entry_price=price,
                        stop_loss=price - atr * 1.5, take_profit=price + atr * 3.0,
                        reason=f"거래량 급증 상승(×{cur_vol/avg_vol:.1f}, +{ret*100:.1f}%)",
                        timeframe=timeframe)
                if ret < -self.params["min_ret"]:
                    return self._create_signal(
                        signal=SignalType.SELL, score=-0.80, confidence=0.75,
                        market=market, entry_price=price,
                        stop_loss=price + atr * 1.5, take_profit=price - atr * 3.0,
                        reason=f"거래량 급증 하락(×{cur_vol/avg_vol:.1f}, {ret*100:.1f}%)",
                        timeframe=timeframe)
        except Exception:
            pass
        return None
