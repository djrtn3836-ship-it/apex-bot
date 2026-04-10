"""Market Regime Detection (  )
v2.1.0"""
import pandas as pd
import numpy as np
from typing import Dict
from .base_strategy import BaseStrategy


class MarketRegimeDetector(BaseStrategy):
    """Bull/Bear/Range/Volatile 4"""
    
    def __init__(self):
        super().__init__()
        self.name = "Market_Regime"
        self.timeframe = "1h"
        
    def analyze(self, df: pd.DataFrame, market: str) -> Dict:
        """1. Bull Trend: SMA20 > SMA50, ATR 
        2. Bear Trend: SMA20 < SMA50, ATR 
        3. Range: SMA20 ≈ SMA50, ATR 
        4. Volatile: ATR"""
        if len(df) < 50:
            return {"signal": 0, "confidence": 0.0, "regime": "unknown"}
        
        sma_20 = df['close'].rolling(20).mean().iloc[-1]
        sma_50 = df['close'].rolling(50).mean().iloc[-1]
        
        # ATR 계산
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        atr_ratio = atr / df['close'].iloc[-1]
        
        # 국면 분류
        if atr_ratio > 0.05:
            regime = "volatile"
            signal = 0
            confidence = 0.0
        elif sma_20 > sma_50 * 1.02:
            regime = "bull"
            signal = 1
            confidence = 0.70
        elif sma_20 < sma_50 * 0.98:
            regime = "bear"
            signal = -1
            confidence = 0.65
        else:
            regime = "range"
            signal = 0
            confidence = 0.0
        
        return {
            "signal": signal,
            "confidence": confidence,
            "regime": regime,
            "reason": f"시장 국면: {regime}"
        }