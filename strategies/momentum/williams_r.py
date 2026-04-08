"""
Williams %R 전략 - 라리 윌리엄스
과매수/과매도 + 다이버전스
"""
from typing import Dict, Optional
from signals.base_strategy import BaseStrategy, SignalType
import pandas as pd

class WilliamsRStrategy(BaseStrategy):
    """Williams %R 오실레이터 전략"""
    
    def __init__(self, period: int = 14):
        super().__init__()
        self.period = period
        self.name = "Williams_R"
    
    def calculate_indicator(self, df: pd.DataFrame) -> pd.DataFrame:
        """Williams %R 계산"""
        high = df["high"].rolling(self.period).max()
        low = df["low"].rolling(self.period).min()
        df["williams_r"] = -100 * (high - df["close"]) / (high - low)
        return df
    
    def generate_signal(self, df: pd.DataFrame, **kwargs) -> Optional[Dict]:
        """신호 생성"""
        df = self.calculate_indicator(df)
        
        if len(df) < self.period + 5:
            return None
        
        wr = df["williams_r"].iloc[-1]
        wr_prev = df["williams_r"].iloc[-2]
        
        # 과매도 (-80 이하) → 매수
        if wr < -80 and wr_prev < wr:  # 상승 전환
            return {
                "signal": SignalType.BUY,
                "strength": min(abs(wr + 100) / 20, 1.0),  # -100에 가까울수록 강함
                "reason": f"Williams %R 과매도 반등 ({wr:.1f})",
            }
        
        # 과매수 (-20 이상) → 매도
        elif wr > -20 and wr_prev > wr:  # 하락 전환
            return {
                "signal": SignalType.SELL,
                "strength": min(abs(wr) / 20, 1.0),
                "reason": f"Williams %R 과매수 ({wr:.1f})",
            }
        
        return None
