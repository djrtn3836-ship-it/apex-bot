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
            atr    = float(pd.concat([df["high"]-df["low"],(df["high"]-df["close"].shift()).abs(),(df["low"]-df["close"].shift()).abs()],axis=1).max(axis=1).rolling(14).mean().iloc[-1]) or price * 0.02

            squeezed   = float(width.iloc[-1]) < self.params["squeeze_pct"]
            cur_bb_pct = float(bb_pct.iloc[-1])

            if squeezed and cur_bb_pct < 0.2:
                # ✅ 동적 score: BB 하단 극단값에 가까울수록 높은 score
                extreme = (0.2 - cur_bb_pct) / 0.2
                score   = round(min(0.60 + extreme * 0.35, 0.95), 3)
                conf    = round(min(0.65 + extreme * 0.25, 0.92), 3)
                return self._create_signal(
                    signal=SignalType.BUY, score=score, confidence=conf,
                    market=market, entry_price=price,
                    stop_loss=price - atr * 1.5, take_profit=price + atr * 3.0,
                    reason=f"BB 스퀴즈 하단({cur_bb_pct:.2f})", timeframe=timeframe)
            if squeezed and cur_bb_pct > 0.8:
                # ✅ 동적 score: BB 상단 극단값에 가까울수록 높은 score
                extreme = (cur_bb_pct - 0.8) / 0.2
                score   = round(min(0.60 + extreme * 0.35, 0.95), 3)
                conf    = round(min(0.65 + extreme * 0.25, 0.92), 3)
                return self._create_signal(
                    signal=SignalType.SELL, score=-score, confidence=conf,
                    market=market, entry_price=price,
                    stop_loss=price + atr * 1.5, take_profit=price - atr * 3.0,
                    reason=f"BB 스퀴즈 상단({cur_bb_pct:.2f})", timeframe=timeframe)
        except Exception:
            pass
        return None
