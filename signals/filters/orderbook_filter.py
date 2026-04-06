"""
APEX BOT - 호가창 필터
호가창 분석 결과를 매수/매도 신호 필터로 활용
"""
from typing import Optional
from loguru import logger
from data.processors.orderbook_analyzer import OrderBookAnalyzer, OrderBookSignal


class OrderBookFilter:
    """
    호가창 기반 매매 필터
    
    - 매수 차단: 강한 매도벽 존재, 매도 스푸핑 감지
    - 매수 허용: 매수벽 지지, 호가 불균형 매수 우세
    - 매도 신호: 매수벽 붕괴, 매도 스푸핑
    """
    
    def __init__(self, analyzer: OrderBookAnalyzer = None):
        self.analyzer = analyzer or OrderBookAnalyzer()
    
    def can_buy(self, market: str, orderbook: dict = None) -> tuple:
        """
        매수 가능 여부 판단
        Returns: (can_buy: bool, reason: str, signal: OrderBookSignal)
        """
        if orderbook:
            sig = self.analyzer.analyze(market, orderbook)
        else:
            sig = self.analyzer.get_signal(market)
        
        if not sig:
            return True, "호가창 데이터 없음 (통과)", None
        
        # 강한 매도 스푸핑 → 매수 차단
        if sig.spoofing_detected and sig.spoofing_side == "SELL_SPOOF":
            return False, f"매도 스푸핑 감지 - 매수 차단", sig
        
        # 강한 매도 압력 → 매수 차단
        if sig.pressure == "STRONG_SELL" and sig.imbalance_ratio < -0.4:
            return False, f"강한 매도 압력 (불균형={sig.imbalance_ratio:.2f})", sig
        
        # 대형 매도벽 존재 → 경고 (차단하지 않고 신뢰도 감소)
        if sig.ask_wall_price > 0:
            return True, f"매도벽 주의 ({sig.ask_wall_price:,})", sig
        
        # 매수벽 지지 + 매수 우세 → 적극 허용
        if sig.pressure in ("BUY", "STRONG_BUY"):
            return True, f"매수 압력 우세 (불균형={sig.imbalance_ratio:.2f})", sig
        
        return True, "호가창 중립", sig
    
    def get_confidence_boost(self, market: str) -> float:
        """
        호가창 기반 신뢰도 보정
        Returns: -0.2 ~ +0.2 범위의 신뢰도 조정값
        """
        sig = self.analyzer.get_signal(market)
        if not sig:
            return 0.0
        
        boost = 0.0
        
        # 매수 압력에 따른 보정
        if sig.pressure == "STRONG_BUY":
            boost += 0.15
        elif sig.pressure == "BUY":
            boost += 0.08
        elif sig.pressure == "STRONG_SELL":
            boost -= 0.15
        elif sig.pressure == "SELL":
            boost -= 0.08
        
        # 스푸핑 감지시 반대 방향 보정
        if sig.spoofing_detected:
            if sig.spoofing_side == "BUY_SPOOF":
                boost -= 0.10  # 매수 스푸핑 → 실제 하락 가능
            elif sig.spoofing_side == "SELL_SPOOF":
                boost += 0.10  # 매도 스푸핑 → 실제 상승 가능
        
        # 벽 돌파시 추가 보정
        if sig.wall_breakout:
            if sig.wall_breakout_side == "BULL_BREAKOUT":
                boost += 0.20
            elif sig.wall_breakout_side == "BEAR_BREAKOUT":
                boost -= 0.20
        
        return max(-0.25, min(0.25, boost))
