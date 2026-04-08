"""
엘리엇 파동 기초 감지 (v2.0.4)
5파 상승 패턴 감지 (간단한 지그재그 카운팅)
"""
import numpy as np
from typing import List, Optional


class ElliottWaveDetector:
    """엘리엇 파동 5파 상승 감지"""
    
    def __init__(self):
        self.min_wave_bars = 5  # 최소 파동 길이
        
    def detect_impulse_wave(self, closes: List[float]) -> Optional[str]:
        """
        5파 상승 임펄스 감지
        Returns: "wave5_top" | "wave4_bottom" | None
        """
        if len(closes) < 50:
            return None
            
        # 최근 50봉에서 지그재그 피크/밸리 찾기
        peaks, valleys = self._find_zigzag(closes[-50:])
        
        if len(peaks) < 3 or len(valleys) < 2:
            return None
            
        # 패턴: 밸리-피크-밸리-피크-밸리-피크 (5파)
        # 간단 휴리스틱: 피크가 3개 이상 + 마지막 피크가 최고점
        if peaks[-1] > peaks[-2] > peaks[-3]:
            # 5파 정점 후보
            if closes[-1] < closes[peaks[-1]] * 0.98:
                return "wave5_top"  # 5파 정점 형성 후 하락 시작
                
        # 4파 조정 감지 (매수 기회)
        if valleys[-1] > valleys[-2] and peaks[-1] > peaks[-2]:
            if abs(closes[-1] - closes[valleys[-1]]) < closes[valleys[-1]] * 0.02:
                return "wave4_bottom"  # 4파 조정 종료, 5파 시작 예상
                
        return None
        
    def _find_zigzag(self, closes: List[float], threshold=0.03):
        """
        지그재그 피크/밸리 찾기
        threshold: 최소 변동률 (3%)
        """
        peaks = []
        valleys = []
        
        trend = 0  # 0:중립, 1:상승, -1:하락
        last_extreme_idx = 0
        last_extreme_price = closes[0]
        
        for i in range(1, len(closes)):
            if trend == 0:
                if closes[i] > last_extreme_price * (1 + threshold):
                    trend = 1
                    valleys.append(last_extreme_idx)
                    last_extreme_idx = i
                    last_extreme_price = closes[i]
                elif closes[i] < last_extreme_price * (1 - threshold):
                    trend = -1
                    peaks.append(last_extreme_idx)
                    last_extreme_idx = i
                    last_extreme_price = closes[i]
            elif trend == 1:  # 상승 중
                if closes[i] > last_extreme_price:
                    last_extreme_idx = i
                    last_extreme_price = closes[i]
                elif closes[i] < last_extreme_price * (1 - threshold):
                    trend = -1
                    peaks.append(last_extreme_idx)
                    last_extreme_idx = i
                    last_extreme_price = closes[i]
            else:  # 하락 중
                if closes[i] < last_extreme_price:
                    last_extreme_idx = i
                    last_extreme_price = closes[i]
                elif closes[i] > last_extreme_price * (1 + threshold):
                    trend = 1
                    valleys.append(last_extreme_idx)
                    last_extreme_idx = i
                    last_extreme_price = closes[i]
                    
        return peaks, valleys