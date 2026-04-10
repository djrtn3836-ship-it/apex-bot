"""Fibonacci Retracement ( ) 
v2.1.0"""
import pandas as pd
import numpy as np
from typing import Dict
from .base_strategy import BaseStrategy


class FibonacciRetracementStrategy(BaseStrategy):
    """docstring"""
    
    def __init__(self):
        super().__init__()
        self.name = "Fibonacci_Retracement"
        self.timeframe = "4h"
        
    def analyze(self, df: pd.DataFrame, market: str) -> Dict:
        """38.2%  = 
          61.8%  ="""
        if len(df) < 50:
            return {"signal": 0, "confidence": 0.0}
        
        # 최근 50봉 기준
        recent = df.tail(50)
        high = recent['high'].max()
        low = recent['low'].min()
        diff = high - low
        
        # 피보나치 레벨
        fib_levels = {
            0.236: high - diff * 0.236,
            0.382: high - diff * 0.382,
            0.5: high - diff * 0.5,
            0.618: high - diff * 0.618,
            0.786: high - diff * 0.786
        }
        
        close = df['close'].iloc[-1]
        prev_close = df['close'].iloc[-2]
        
        # 38.2% 레벨 근처 반등
        if abs(close - fib_levels[0.382]) < diff * 0.02:
            if close > prev_close:
                return {
                    "signal": 1,
                    "confidence": 0.65,
                    "reason": f"Fib 38.2% 반등 ({fib_levels[0.382]:.0f})"
                }
        
        # 61.8% 레벨 근처 저항
        if abs(close - fib_levels[0.618]) < diff * 0.02:
            if close < prev_close:
                return {
                    "signal": -1,
                    "confidence": 0.60,
                    "reason": f"Fib 61.8% 저항 ({fib_levels[0.618]:.0f})"
                }
        
        return {"signal": 0, "confidence": 0.0}