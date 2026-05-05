"""APEX BOT -   
POC(Point of Control), HVN/LVN, Value Area"""
import numpy as np
import pandas as pd
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from loguru import logger


@dataclass
class VolumeProfileResult:
    poc_price: float        # Point of Control (최대 거래량 가격)
    vah: float             # Value Area High (가치 영역 상단)
    val: float             # Value Area Low (가치 영역 하단)
    above_poc: bool        # 현재가 POC 위
    hvn_levels: list       # High Volume Node (지지/저항)
    lvn_levels: list       # Low Volume Node (돌파 가능 영역)
    

class VolumeProfileAnalyzer:
    """- POC:     ( /)
    - Value Area:   70%  
    - HVN:    (/ )
    - LVN:    (  )"""
    
    def __init__(self, bins: int = 50, value_area_pct: float = 0.70):
        self.bins = bins
        self.value_area_pct = value_area_pct
    
    def analyze(self, df: pd.DataFrame) -> Optional[VolumeProfileResult]:
        """analyze 실행"""
        try:
            if len(df) < 20:
                return None
            
            high = df["high"].values
            low = df["low"].values
            volume = df["volume"].values
            close = df["close"].values
            
            # 가격 구간 설정
            price_min = low.min()
            price_max = high.max()
            price_range = np.linspace(price_min, price_max, self.bins + 1)
            
            # 각 가격 구간별 거래량 집계
            vol_profile = np.zeros(self.bins)
            
            for i in range(len(df)):
                # 캔들이 걸치는 구간에 거래량 분배
                candle_low = low[i]
                candle_high = high[i]
                candle_vol = volume[i]
                
                for j in range(self.bins):
                    bin_low = price_range[j]
                    bin_high = price_range[j + 1]
                    
                    # 캔들과 구간의 겹치는 비율
                    overlap_low = max(candle_low, bin_low)
                    overlap_high = min(candle_high, bin_high)
                    
                    if overlap_high > overlap_low:
                        candle_range = candle_high - candle_low + 1e-8
                        overlap_ratio = (overlap_high - overlap_low) / candle_range
                        vol_profile[j] += candle_vol * overlap_ratio
            
            # POC 계산
            poc_idx = np.argmax(vol_profile)
            poc_price = (price_range[poc_idx] + price_range[poc_idx + 1]) / 2
            
            # Value Area 계산 (POC에서 확장)
            total_vol = vol_profile.sum()
            target_vol = total_vol * self.value_area_pct
            
            va_low_idx = poc_idx
            va_high_idx = poc_idx
            accumulated = vol_profile[poc_idx]
            
            while accumulated < target_vol:
                expand_up = va_high_idx < self.bins - 1
                expand_down = va_low_idx > 0
                
                if not expand_up and not expand_down:
                    break
                
                up_vol = vol_profile[va_high_idx + 1] if expand_up else 0
                down_vol = vol_profile[va_low_idx - 1] if expand_down else 0
                
                if up_vol >= down_vol and expand_up:
                    va_high_idx += 1
                    accumulated += up_vol
                elif expand_down:
                    va_low_idx -= 1
                    accumulated += down_vol
                else:
                    break
            
            vah = (price_range[va_high_idx] + price_range[va_high_idx + 1]) / 2
            val = (price_range[va_low_idx] + price_range[va_low_idx + 1]) / 2
            
            # HVN/LVN 감지
            avg_vol = np.mean(vol_profile)
            hvn_levels = []
            lvn_levels = []
            
            for j in range(self.bins):
                mid_price = (price_range[j] + price_range[j + 1]) / 2
                if vol_profile[j] > avg_vol * 1.5:
                    hvn_levels.append(mid_price)
                elif vol_profile[j] < avg_vol * 0.5:
                    lvn_levels.append(mid_price)
            
            current_price = close[-1]
            
            return VolumeProfileResult(
                poc_price=poc_price,
                vah=vah,
                val=val,
                above_poc=current_price > poc_price,
                hvn_levels=hvn_levels,
                lvn_levels=lvn_levels
            )
            
        except Exception as e:
            logger.debug(f"  : {e}")
            return None
    
    def get_nearest_support_resistance(self, df: pd.DataFrame, current_price: float) -> Dict:
        """/"""
        result = self.analyze(df)
        if not result:
            return {}
        
        support = max([p for p in result.hvn_levels if p < current_price], default=result.val)
        resistance = min([p for p in result.hvn_levels if p > current_price], default=result.vah)
        
        return {
            "poc": result.poc_price,
            "support": support,
            "resistance": resistance,
            "vah": result.vah,
            "val": result.val,
            "above_poc": result.above_poc,
            "risk_reward": (resistance - current_price) / (current_price - support + 1e-8)
        }
