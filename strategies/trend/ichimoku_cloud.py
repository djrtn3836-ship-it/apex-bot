"""
Ichimoku Cloud (일목균형표) 전략
v2.1.0 추가
"""
import pandas as pd
from typing import Dict, Optional
from .base_strategy import BaseStrategy


class IchimokuCloudStrategy(BaseStrategy):
    """일목균형표 구름대 돌파 전략"""
    
    def __init__(self):
        super().__init__()
        self.name = "Ichimoku_Cloud"
        self.timeframe = "1h"
        
    def analyze(self, df: pd.DataFrame, market: str) -> Dict:
        """
        구름대 상향 돌파 = 매수
        구름대 하향 이탈 = 매도
        """
        if len(df) < 52:
            return {"signal": 0, "confidence": 0.0}
        
        # 전환선 (9일)
        high_9 = df['high'].rolling(9).max()
        low_9 = df['low'].rolling(9).min()
        tenkan = (high_9 + low_9) / 2
        
        # 기준선 (26일)
        high_26 = df['high'].rolling(26).max()
        low_26 = df['low'].rolling(26).min()
        kijun = (high_26 + low_26) / 2
        
        # 선행스팬1 (전환선 + 기준선) / 2, 26일 앞
        senkou_a = ((tenkan + kijun) / 2).shift(26)
        
        # 선행스팬2 (52일 최고최저 평균), 26일 앞
        high_52 = df['high'].rolling(52).max()
        low_52 = df['low'].rolling(52).min()
        senkou_b = ((high_52 + low_52) / 2).shift(26)
        
        # 후행스팬 (종가, 26일 뒤)
        chikou = df['close'].shift(-26)
        
        close = df['close'].iloc[-1]
        cloud_top = max(senkou_a.iloc[-1], senkou_b.iloc[-1])
        cloud_bottom = min(senkou_a.iloc[-1], senkou_b.iloc[-1])
        
        # 신호 생성
        if close > cloud_top and tenkan.iloc[-1] > kijun.iloc[-1]:
            # 구름대 위 + 전환선 > 기준선 = 강세
            return {
                "signal": 1,
                "confidence": 0.75,
                "reason": f"Ichimoku 상향돌파 (구름: {cloud_top:.0f})"
            }
        elif close < cloud_bottom and tenkan.iloc[-1] < kijun.iloc[-1]:
            # 구름대 아래 + 전환선 < 기준선 = 약세
            return {
                "signal": -1,
                "confidence": 0.70,
                "reason": f"Ichimoku 하향이탈 (구름: {cloud_bottom:.0f})"
            }
        
        return {"signal": 0, "confidence": 0.0}