"""
Multi-Timeframe Confirmation (다중 타임프레임 확인)
v2.1.0 추가
"""
from typing import Dict, List
import pandas as pd


class MultiTimeframeConfirmation:
    """5분/15분/1시간 3개 타임프레임 동의 확인"""
    
    def __init__(self):
        self.timeframes = ["5m", "15m", "1h"]
        
    def confirm_signal(
        self,
        signals_5m: Dict,
        signals_15m: Dict,
        signals_1h: Dict
    ) -> Dict:
        """
        3개 타임프레임 모두 동의 시 신뢰도 +30%
        2개 동의 시 신뢰도 +15%
        """
        sig_5 = signals_5m.get("signal", 0)
        sig_15 = signals_15m.get("signal", 0)
        sig_1h = signals_1h.get("signal", 0)
        
        if sig_5 == sig_15 == sig_1h and sig_5 != 0:
            # 3개 모두 동의
            base_conf = max(
                signals_5m.get("confidence", 0),
                signals_15m.get("confidence", 0),
                signals_1h.get("confidence", 0)
            )
            return {
                "signal": sig_5,
                "confidence": min(0.95, base_conf + 0.30),
                "reason": "MTF 3개 동의"
            }
        elif (sig_5 == sig_15 or sig_5 == sig_1h or sig_15 == sig_1h) and sig_5 != 0:
            # 2개 동의
            base_conf = max(
                signals_5m.get("confidence", 0),
                signals_15m.get("confidence", 0)
            )
            return {
                "signal": sig_5 if sig_5 == sig_15 else sig_1h,
                "confidence": min(0.80, base_conf + 0.15),
                "reason": "MTF 2개 동의"
            }
        
        return {"signal": 0, "confidence": 0.0}