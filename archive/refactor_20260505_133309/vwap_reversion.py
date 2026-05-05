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
            tp     = (df["high"] + df["low"] + df["close"]) / 3
            vol    = df["volume"] if "volume" in df.columns else pd.Series(
                [1.0]*len(df), index=df.index)
            vwap   = (tp * vol).cumsum() / (vol.cumsum() + 1e-9)
            price  = float(df["close"].iloc[-1])
            vwap_v = float(vwap.iloc[-1])
            dev    = (price - vwap_v) / (vwap_v + 1e-9)
            atr    = float(pd.concat([df["high"]-df["low"],(df["high"]-df["close"].shift()).abs(),(df["low"]-df["close"].shift()).abs()],axis=1).max(axis=1).rolling(14).mean().iloc[-1]) or price * 0.02

            # 레짐 컨텍스트 확인 (외부에서 주입된 regime 속성 활용)
            regime = getattr(self, '_current_regime', None)
            regime_str = str(regime).upper() if regime else ''

            # RSI 과매도/과매수 확인 (추가 컨펌)
            rsi = None
            if len(df) >= 14:
                delta = df["close"].diff()
                gain  = delta.where(delta > 0, 0).rolling(14).mean()
                loss  = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rs    = gain / (loss + 1e-9)
                rsi   = float(100 - 100 / (1 + rs.iloc[-1]))

            if dev < -self.params["dev_threshold"]:
                # TRENDING_DOWN 레짐에서는 BUY 차단 (추세 역행 방지)
                if 'TRENDING_DOWN' in regime_str:
                    return None
                # RSI 과매도(< 35) 확인 시 신뢰도 상향
                rsi_boost = 0.06 if (rsi is not None and rsi < 35) else 0.0
                depth = min(abs(dev) / 0.10, 1.0)
                score = round(min(0.55 + depth * 0.40 + rsi_boost, 0.95), 3)
                conf  = round(min(0.55 + depth * 0.37 + rsi_boost, 0.92), 3)
                return self._create_signal(
                    signal=SignalType.BUY, score=score, confidence=conf,
                    market=market, entry_price=price,
                    stop_loss=price - atr * 1.5, take_profit=vwap_v,
                    reason=f"VWAP 하방이탈({dev*100:.1f}% RSI={rsi:.0f if rsi else '?'})",
                    timeframe=timeframe)
            if dev > self.params["dev_threshold"]:
                # TRENDING_UP 레짐에서는 SELL 차단 (추세 역행 방지)
                if 'TRENDING_UP' in regime_str:
                    return None
                # RSI 과매수(> 65) 확인 시 신뢰도 상향
                rsi_boost = 0.06 if (rsi is not None and rsi > 65) else 0.0
                depth = min(abs(dev) / 0.10, 1.0)
                score = round(min(0.55 + depth * 0.40 + rsi_boost, 0.95), 3)
                conf  = round(min(0.55 + depth * 0.37 + rsi_boost, 0.92), 3)
                return self._create_signal(
                    signal=SignalType.SELL, score=-score, confidence=conf,
                    market=market, entry_price=price,
                    stop_loss=price + atr * 1.5, take_profit=vwap_v,
                    reason=f"VWAP 상방이탈({dev*100:.1f}% RSI={rsi:.0f if rsi else '?'})",
                    timeframe=timeframe)
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"VWAP signal error: {e}")
        return None
