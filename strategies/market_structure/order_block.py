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

            # 볼륨 컨펌: 평균 대비 1.3배 이상 = 강한 오더블록
            vol_ratio = 1.0
            if "volume" in df.columns:
                vol_avg   = float(df["volume"].rolling(20).mean().iloc[-1]) or 1.0
                vol_ratio = float(df["volume"].iloc[-1]) / (vol_avg + 1e-9)
            vol_boost = 0.08 if vol_ratio >= 1.3 else (0.04 if vol_ratio >= 1.1 else 0.0)
        # [FIX] 거래량 0 또는 극소량 시 신호 차단
        if vol_ratio < 0.3:
            return None

            # 동적 포지션 크기 힌트 (score에 반영)
            # vol_ratio 높을수록 score 상향 → engine이 포지션 크기 결정에 활용
            if not bull_ob.empty:
                ob_low      = float(bull_ob["low"].iloc[-1])
                touch_dist  = abs(price - ob_low) / (ob_low + 1e-9)
                if touch_dist < self.params["touch_pct"]:
                    body_str = float(ratio[bull_ob.index[-1]])
                    prox     = 1.0 - (touch_dist / self.params["touch_pct"])
                    score    = round(min(0.55 + body_str * 0.25 + prox * 0.15 + vol_boost, 0.95), 3)
                    conf     = round(min(0.60 + body_str * 0.20 + prox * 0.12 + vol_boost, 0.93), 3)
                    return self._create_signal(
                        signal=SignalType.BUY, score=score, confidence=conf,
                        market=market, entry_price=price,
                        stop_loss=ob_low - atr, take_profit=price + atr * 3.0,
                        reason=f"불리시 OB(바디={body_str:.2f} vol={vol_ratio:.1f}x prox={prox:.2f})",
                        timeframe=timeframe)
            if not bear_ob.empty:
                ob_high     = float(bear_ob["high"].iloc[-1])
                touch_dist  = abs(price - ob_high) / (ob_high + 1e-9)
                if touch_dist < self.params["touch_pct"]:
                    body_str = float(ratio[bear_ob.index[-1]])
                    prox     = 1.0 - (touch_dist / self.params["touch_pct"])
                    score    = round(min(0.55 + body_str * 0.25 + prox * 0.15 + vol_boost, 0.95), 3)
                    conf     = round(min(0.60 + body_str * 0.20 + prox * 0.12 + vol_boost, 0.93), 3)
                    return self._create_signal(
                        signal=SignalType.SELL, score=-score, confidence=conf,
                        market=market, entry_price=price,
                        stop_loss=ob_high + atr, take_profit=price - atr * 3.0,
                        reason=f"베어리시 OB(바디={body_str:.2f} vol={vol_ratio:.1f}x prox={prox:.2f})",
                        timeframe=timeframe)
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"OrderBlock signal error: {e}")
        return None
