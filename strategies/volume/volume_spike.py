"""
Volume Spike Detection (거래량 급등 감지)
v2.1.0 강화
"""
import pandas as pd
from typing import Dict
from ..base_strategy import BaseStrategy


class VolumeSpikeDetector(BaseStrategy):
    """거래량 3배 이상 급등 포착"""
    
    def __init__(self):
        super().__init__()
        self.name = "Volume_Spike"
        self.timeframe = "5m"
        
    def analyze(self, df: pd.DataFrame, market: str) -> Dict:
        """
        거래량 > 20일 평균 × 3.0 + 가격 상승 = 매수
        """
        if len(df) < 20:
            return {"signal": 0, "confidence": 0.0}
        
        avg_volume = df['volume'].rolling(20).mean().iloc[-1]
        current_volume = df['volume'].iloc[-1]
        
        if current_volume > avg_volume * 3.0:
            close = df['close'].iloc[-1]
            prev_close = df['close'].iloc[-2]
            
            if close > prev_close:
                ratio = current_volume / avg_volume
                return {
                    "signal": 1,
                    "confidence": min(0.85, 0.5 + ratio * 0.05),
                    "reason": f"거래량 급등 ({ratio:.1f}배)"
                }
        
        return {"signal": 0, "confidence": 0.0}