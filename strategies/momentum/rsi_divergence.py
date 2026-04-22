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

            # 실제 다이버전스 감지 (최근 20봉 내 가격/RSI 고저점 비교)
            lb = min(20, len(df) - 1)
            price_lows  = df["close"].iloc[-lb:]
            rsi_lows    = rsi.iloc[-lb:]
            price_highs = df["close"].iloc[-lb:]
            rsi_highs   = rsi.iloc[-lb:]

            # 불리시 다이버전스: 가격 신저점 + RSI 고저점
            price_new_low = float(price_lows.iloc[-1]) <= float(price_lows.min())
            rsi_higher    = float(rsi_lows.iloc[-1])  >  float(rsi_lows.min())
            bull_diverge  = price_new_low and rsi_higher

            # 베어리시 다이버전스: 가격 신고점 + RSI 저고점
            price_new_high = float(price_highs.iloc[-1]) >= float(price_highs.max())
            rsi_lower      = float(rsi_highs.iloc[-1])   <  float(rsi_highs.max())
            bear_diverge   = price_new_high and rsi_lower

            # MACD 다이버전스 확인 (복합 컨펌)
            macd_bull = False
            macd_bear = False
            if len(df) >= 26:
                exp1 = df["close"].ewm(span=12, adjust=False).mean()
                exp2 = df["close"].ewm(span=26, adjust=False).mean()
                macd_hist = (exp1 - exp2) - (exp1 - exp2).ewm(span=9, adjust=False).mean()
                macd_bull = float(macd_hist.iloc[-1]) > float(macd_hist.iloc[-2])  # 히스토그램 상승
                macd_bear = float(macd_hist.iloc[-1]) < float(macd_hist.iloc[-2])  # 히스토그램 하락

            if cur < self.params["oversold"]:
                depth = (self.params["oversold"] - cur) / self.params["oversold"]
                # 복합 다이버전스 보너스
                div_boost = 0.10 if bull_diverge else 0.0
                macd_boost = 0.05 if macd_bull else 0.0
                score = round(min(0.55 + depth * 0.40 + div_boost + macd_boost, 0.95), 3)
                conf  = round(min(0.50 + depth * 0.45 + div_boost + macd_boost, 0.93), 3)
                reason = f"RSI 과매도({cur:.1f})"
                if bull_diverge: reason += " +불리시다이버전스"
                if macd_bull:    reason += " +MACD컨펌"
                return self._create_signal(
                    signal=SignalType.BUY, score=score, confidence=conf,
                    market=market, entry_price=price,
                    stop_loss=price - atr * 1.5, take_profit=price + atr * 3.0,
                    reason=reason, timeframe=timeframe)
            if cur > self.params["overbought"]:
                depth = (cur - self.params["overbought"]) / (100 - self.params["overbought"])
                div_boost  = 0.10 if bear_diverge else 0.0
                macd_boost = 0.05 if macd_bear else 0.0
                score = round(min(0.55 + depth * 0.40 + div_boost + macd_boost, 0.95), 3)
                conf  = round(min(0.50 + depth * 0.45 + div_boost + macd_boost, 0.93), 3)
                reason = f"RSI 과매수({cur:.1f})"
                if bear_diverge: reason += " +베어리시다이버전스"
                if macd_bear:    reason += " +MACD컨펌"
                return self._create_signal(
                    signal=SignalType.SELL, score=-score, confidence=conf,
                    market=market, entry_price=price,
                    stop_loss=price + atr * 1.5, take_profit=price - atr * 3.0,
                    reason=reason, timeframe=timeframe)
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"RSI_Divergence signal error: {e}")
        return None
