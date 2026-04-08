from datetime import datetime
from typing import Optional
import pandas as pd
from strategies.base_strategy import BaseStrategy, StrategySignal, SignalType


class MACDCrossStrategy(BaseStrategy):
    NAME = "MACD_Cross"
    DESCRIPTION = "MACD 골든/데드 크로스 전략"
    WEIGHT = 1.5
    MIN_CANDLES = 50

    def _default_params(self) -> dict:
        return {"fast": 12, "slow": 26, "signal": 9}

    def generate_signal(self, df: pd.DataFrame, market: str,
                        timeframe: str = "60") -> Optional[StrategySignal]:
        if df is None or len(df) < self.MIN_CANDLES:
            return None
        try:
            close = df["close"]
            exp1  = close.ewm(span=self.params["fast"],   adjust=False).mean()
            exp2  = close.ewm(span=self.params["slow"],   adjust=False).mean()
            macd  = exp1 - exp2
            sig   = macd.ewm(span=self.params["signal"],  adjust=False).mean()
            hist  = macd - sig
            price = float(close.iloc[-1])
            atr   = float(pd.concat([df["high"]-df["low"],(df["high"]-df["close"].shift()).abs(),(df["low"]-df["close"].shift()).abs()],axis=1).max(axis=1).rolling(14).mean().iloc[-1]) or price * 0.02

            # ✅ 동적 score: 히스토그램 크기를 ATR 대비 비율로 계산
            hist_strength = min(abs(float(hist.iloc[-1])) / (atr + 1e-9), 1.0)
            score = round(min(0.60 + hist_strength * 0.35, 0.95), 3)

            if hist.iloc[-1] > 0 and hist.iloc[-2] <= 0:
                # ✅ 동적 confidence: 히스토그램 기울기 반영
                slope = float(hist.iloc[-1]) - float(hist.iloc[-2])
                conf  = min(0.60 + (slope / (atr + 1e-9)) * 0.3, 0.92)
                return self._create_signal(
                    signal=SignalType.BUY, score=score, confidence=conf,
                    market=market, entry_price=price,
                    stop_loss=price - atr * 1.5, take_profit=price + atr * 3.0,
                    reason=f"MACD 골든크로스(hist={float(hist.iloc[-1]):.4f})", timeframe=timeframe)
            if hist.iloc[-1] < 0 and hist.iloc[-2] >= 0:
                slope = float(hist.iloc[-2]) - float(hist.iloc[-1])
                conf  = min(0.60 + (slope / (atr + 1e-9)) * 0.3, 0.92)
                return self._create_signal(
                    signal=SignalType.SELL, score=-score, confidence=conf,
                    market=market, entry_price=price,
                    stop_loss=price + atr * 1.5, take_profit=price - atr * 3.0,
                    reason=f"MACD 데드크로스(hist={float(hist.iloc[-1]):.4f})", timeframe=timeframe)
        except Exception:
            pass
        return None
