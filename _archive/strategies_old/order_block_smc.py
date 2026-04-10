# strategies/order_block_smc.py
import pandas as pd
import numpy as np
from typing import Optional, List, Tuple
from .base_strategy import BaseStrategy
from signals.signal_combiner import CombinedSignal, SignalType


class OrderBlockSMC(BaseStrategy):
    """+ SMC(Smart Money Concept)"""

    def __init__(self, settings: dict = None):
        super().__init__("OrderBlockSMC", settings)
        self.ob_lookback = self.settings.get("ob_lookback", 20)
        self.ob_touch_tolerance = self.settings.get("ob_touch_tolerance", 0.003)
        self.bos_confirm = self.settings.get("bos_confirm", True)

    def analyze(self, market: str, df: pd.DataFrame, additional_data: dict = None) -> Optional[CombinedSignal]:
        if not self._validate_df(df, min_rows=self.ob_lookback + 10):
            return None

        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        open_price = df["open"].values

        # 오더 블록 탐지
        bull_obs = self._find_bullish_order_blocks(open_price, high, low, close, self.ob_lookback)
        bear_obs = self._find_bearish_order_blocks(open_price, high, low, close, self.ob_lookback)

        current_price = close[-1]
        score = 0.0
        signal_type = None

        # 강세 오더 블록 근처에서 가격 반응 → BUY
        for ob_high, ob_low, ob_strength in bull_obs:
            if ob_low * (1 - self.ob_touch_tolerance) <= current_price <= ob_high * (1 + self.ob_touch_tolerance):
                # BOS(구조 돌파) 확인
                if self.bos_confirm:
                    recent_high = np.max(high[-10:-1])
                    if current_price < recent_high * 0.995:
                        continue
                score = self._normalize_score(0.60 + ob_strength * 0.20)
                signal_type = SignalType.BUY
                break

        # 약세 오더 블록 근처에서 가격 반응 → SELL
        if signal_type is None:
            for ob_high, ob_low, ob_strength in bear_obs:
                if ob_low * (1 - self.ob_touch_tolerance) <= current_price <= ob_high * (1 + self.ob_touch_tolerance):
                    if self.bos_confirm:
                        recent_low = np.min(low[-10:-1])
                        if current_price > recent_low * 1.005:
                            continue
                    score = self._normalize_score(0.60 + ob_strength * 0.20)
                    signal_type = SignalType.SELL
                    break

        if signal_type is None:
            return None

        return CombinedSignal(
            market=market,
            signal_type=signal_type,
            score=score,
            confidence=score * 0.82,
            strategies=["OrderBlockSMC"],
            regime="ORDER_BLOCK",
            metadata={"current_price": current_price, "ob_lookback": self.ob_lookback},
        )

    def _find_bullish_order_blocks(
        self, open_p, high, low, close, lookback
    ) -> List[Tuple[float, float, float]]:
        """:"""
        obs = []
        data_len = len(close)
        for i in range(max(1, data_len - lookback), data_len - 2):
            # 음봉 캔들
            if close[i] >= open_p[i]:
                continue
            # 다음 캔들이 큰 양봉
            next_body = close[i + 1] - open_p[i + 1]
            avg_body = np.mean(np.abs(close[max(0, i - 5):i] - open_p[max(0, i - 5):i])) + 1e-9
            if next_body > avg_body * 1.5:
                strength = min(next_body / avg_body / 3.0, 1.0)
                obs.append((high[i], low[i], strength))
        return obs

    def _find_bearish_order_blocks(
        self, open_p, high, low, close, lookback
    ) -> List[Tuple[float, float, float]]:
        """:"""
        obs = []
        data_len = len(close)
        for i in range(max(1, data_len - lookback), data_len - 2):
            if close[i] <= open_p[i]:
                continue
            next_body = open_p[i + 1] - close[i + 1]
            avg_body = np.mean(np.abs(close[max(0, i - 5):i] - open_p[max(0, i - 5):i])) + 1e-9
            if next_body > avg_body * 1.5:
                strength = min(next_body / avg_body / 3.0, 1.0)
                obs.append((high[i], low[i], strength))
        return obs
