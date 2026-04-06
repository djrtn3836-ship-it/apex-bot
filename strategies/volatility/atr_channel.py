"""
APEX BOT - ATR Channel Breakout Strategy
Keltner Channel + ATR-based volatility breakout
"""
import pandas as pd
import numpy as np
from typing import Optional
from loguru import logger

from strategies.base_strategy import BaseStrategy, StrategySignal, SignalType


class ATRChannelStrategy(BaseStrategy):
    """
    ATR Channel (Keltner Channel variant) Strategy
    - Upper channel breakout  -> momentum buy
    - Lower channel recovery  -> mean-reversion buy
    - Channel squeeze release -> directional entry
    """

    NAME = "ATR_Channel"
    DESCRIPTION = "ATR 채널(켈트너) 변동성 돌파"
    WEIGHT = 1.0
    MIN_CANDLES = 50
    SUPPORTED_TIMEFRAMES = ["60", "240", "1440"]

    def _default_params(self) -> dict:
        return {
            "ema_period": 20,
            "atr_multiplier": 2.0,
            "squeeze_threshold": 0.5,
            "min_adx": 20,
        }

    def generate_signal(
        self, df: pd.DataFrame, market: str, timeframe: str = "60"
    ) -> Optional[StrategySignal]:
        if df is None or len(df) < self.MIN_CANDLES:
            return None

        try:
            df = self._calc_keltner(df)
            last = df.iloc[-1]
            prev = df.iloc[-2]

            close     = float(last["close"])
            kc_upper  = float(last["kc_upper"])
            kc_lower  = float(last["kc_lower"])
            kc_mid    = float(last["kc_mid"])
            rsi       = float(last.get("rsi", 50) or 50)
            adx       = float(last.get("adx", 0) or 0)
            vol_ratio = float(last.get("vol_ratio", 1.0) or 1.0)
            atr       = float(last.get("atr", close * 0.015) or close * 0.015)

            prev_close    = float(prev["close"])
            prev_kc_upper = float(prev.get("kc_upper", kc_upper))
            prev_kc_lower = float(prev.get("kc_lower", kc_lower))

            channel_width = kc_upper - kc_lower
            channel_pos   = (close - kc_lower) / (channel_width + 1e-10)

            # Squeeze detection
            bb_width      = float(last.get("bb_width", 1.0) or 1.0)
            atr_pct       = float(last.get("atr_pct", 1.0) or 1.0) / 100
            p             = self.params
            squeeze_active = bb_width < atr_pct * p["squeeze_threshold"]

            min_adx = p["min_adx"]

            # ── BUY: upper-channel breakout (momentum) ────────────────
            if (close > kc_upper and prev_close <= prev_kc_upper
                    and adx > min_adx and vol_ratio > 1.5):
                conf = min(0.85, 0.65 + (adx - min_adx) * 0.005)
                return self._create_signal(
                    SignalType.BUY, 0.80, conf, market,
                    entry_price=close,
                    stop_loss=close - atr * 1.5,
                    take_profit=close + atr * 3.0,
                    reason=(
                        f"ATR채널 상단 돌파 | close={close:,.0f} > upper={kc_upper:,.0f}"
                        f" | ADX={adx:.1f}"
                    ),
                    timeframe=timeframe,
                    metadata={"channel_pos": channel_pos, "adx": adx,
                               "squeeze": squeeze_active},
                )

            # ── BUY: lower-channel recovery (mean-reversion) ──────────
            if (prev_close < prev_kc_lower and close > kc_lower
                    and rsi < 45 and vol_ratio > 1.2):
                return self._create_signal(
                    SignalType.BUY, 0.70, 0.68, market,
                    entry_price=close,
                    stop_loss=close - atr * 1.0,
                    take_profit=kc_mid,
                    reason=f"ATR채널 하단 복귀 | RSI={rsi:.1f}",
                    timeframe=timeframe,
                    metadata={"channel_pos": channel_pos, "rsi": rsi},
                )

            # ── Squeeze release: directional ──────────────────────────
            if squeeze_active and vol_ratio > 2.0:
                direction = SignalType.BUY if close > kc_mid else SignalType.SELL
                sl = (close - atr * 1.5) if direction == SignalType.BUY else (close + atr * 1.5)
                tp = (close + atr * 3.0) if direction == SignalType.BUY else (close - atr * 3.0)
                return self._create_signal(
                    direction, 0.75, 0.65, market,
                    entry_price=close,
                    stop_loss=sl,
                    take_profit=tp,
                    reason=f"ATR 스퀴즈 해소 | 방향={direction.name}",
                    timeframe=timeframe,
                    metadata={"squeeze": True, "vol_ratio": vol_ratio},
                )

            # ── SELL: upper-channel rejection ─────────────────────────
            if (prev_close > prev_kc_upper and close < kc_upper and rsi > 60):
                return self._create_signal(
                    SignalType.SELL, 0.70, 0.65, market,
                    entry_price=close,
                    stop_loss=close + atr * 1.5,
                    take_profit=close - atr * 3.0,
                    reason=f"ATR채널 상단 이탈 | RSI={rsi:.1f}",
                    timeframe=timeframe,
                    metadata={"channel_pos": channel_pos, "rsi": rsi},
                )

            return None

        except Exception as e:
            logger.error(f"{self.NAME} 오류 ({market}): {e}")
            return None

    # ── Internal helpers ─────────────────────────────────────────────

    def _calc_keltner(self, df: pd.DataFrame) -> pd.DataFrame:
        """Keltner channel calculation (in-place)"""
        p = self.params
        ema_period = p.get("ema_period", 20)
        mult       = p.get("atr_multiplier", 2.0)

        mid = df["close"].ewm(span=ema_period, adjust=False).mean()

        if "atr" in df.columns:
            atr_series = df["atr"]
        else:
            hl = df["high"] - df["low"]
            atr_series = hl.ewm(span=14, adjust=False).mean()

        df = df.copy()
        df["kc_mid"]   = mid
        df["kc_upper"] = mid + mult * atr_series
        df["kc_lower"] = mid - mult * atr_series
        return df
