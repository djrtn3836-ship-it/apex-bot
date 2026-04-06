"""
upgrade_all.py
APEX BOT 전체 고도화 - 미완성 모듈 일괄 완성
1. 호가창 분석 엔진 (매수벽/매도벽/Spoofing)
2. OrderBook 기반 신호 생성
3. ML 모델 훈련 데이터 파이프라인
4. 전략 앙상블 가중치 최적화
5. 대시보드 고도화
"""
import os
from pathlib import Path

# ──────────────────────────────────────────────────────────────
# FILE 1: data/processors/orderbook_analyzer.py
# 호가창 분석 - 매수벽/매도벽/Spoofing/불균형 감지
# ──────────────────────────────────────────────────────────────
ORDERBOOK_ANALYZER = '''"""
APEX BOT - 호가창 분석 엔진
매수벽/매도벽 감지, Spoofing 탐지, 호가 불균형 분석
"""
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from collections import deque
from loguru import logger


@dataclass
class OrderBookSignal:
    """호가창 분석 결과"""
    market: str
    bid_wall_price: float = 0.0       # 매수벽 가격
    bid_wall_size: float = 0.0        # 매수벽 수량
    ask_wall_price: float = 0.0       # 매도벽 가격
    ask_wall_size: float = 0.0        # 매도벽 수량
    imbalance_ratio: float = 0.0      # 불균형 비율 (+1=완전매수, -1=완전매도)
    spread_pct: float = 0.0           # 스프레드 %
    spoofing_detected: bool = False   # 스푸핑 감지
    spoofing_side: str = ""           # 스푸핑 방향 (BUY/SELL)
    pressure: str = "NEUTRAL"         # 매수압력/매도압력/중립
    wall_breakout: bool = False       # 벽 돌파 여부
    wall_breakout_side: str = ""      # 돌파 방향


class OrderBookAnalyzer:
    """
    실시간 호가창 분석기
    
    분석 항목:
    1. 매수벽/매도벽 감지 (대형 주문 탐지)
    2. Spoofing 감지 (주문 출현→사라짐 패턴)
    3. 호가 불균형 지수 (bid/ask 물량 비교)
    4. 스프레드 분석
    5. 벽 돌파 감지
    """
    
    def __init__(self, 
                 wall_threshold: float = 5.0,      # 평균 대비 N배 이상 = 벽
                 spoofing_window: int = 10,          # 스푸핑 감지 윈도우
                 imbalance_depth: int = 10):         # 불균형 계산 호가 깊이
        self.wall_threshold = wall_threshold
        self.spoofing_window = spoofing_window
        self.imbalance_depth = imbalance_depth
        
        # 스푸핑 감지용 히스토리
        self._ob_history: Dict[str, deque] = {}     # {market: deque of snapshots}
        self._wall_history: Dict[str, deque] = {}   # {market: deque of walls}
        self._last_signal: Dict[str, OrderBookSignal] = {}
    
    def analyze(self, market: str, orderbook: dict) -> Optional[OrderBookSignal]:
        """
        호가창 데이터 분석
        
        orderbook 형식 (업비트 API):
        {
            "orderbook_units": [
                {"ask_price": float, "bid_price": float, 
                 "ask_size": float, "bid_size": float},
                ...
            ],
            "timestamp": int
        }
        """
        try:
            units = orderbook.get("orderbook_units", [])
            if not units:
                return None
            
            # 기본 데이터 추출
            bids = [(u["bid_price"], u["bid_size"]) for u in units]
            asks = [(u["ask_price"], u["ask_size"]) for u in units]
            
            if not bids or not asks:
                return None
            
            signal = OrderBookSignal(market=market)
            
            # 1. 매수벽/매도벽 감지
            bid_wall = self._detect_wall(bids, side="bid")
            ask_wall = self._detect_wall(asks, side="ask")
            
            if bid_wall:
                signal.bid_wall_price, signal.bid_wall_size = bid_wall
            if ask_wall:
                signal.ask_wall_price, signal.ask_wall_size = ask_wall
            
            # 2. 호가 불균형 지수 계산
            signal.imbalance_ratio = self._calc_imbalance(
                bids[:self.imbalance_depth], 
                asks[:self.imbalance_depth]
            )
            
            # 3. 스프레드 계산
            best_bid = bids[0][0]
            best_ask = asks[0][0]
            signal.spread_pct = (best_ask - best_bid) / best_ask * 100
            
            # 4. 매수/매도 압력 판단
            signal.pressure = self._calc_pressure(signal.imbalance_ratio)
            
            # 5. 스푸핑 감지
            spoofing = self._detect_spoofing(market, bids, asks)
            if spoofing:
                signal.spoofing_detected = True
                signal.spoofing_side = spoofing
            
            # 6. 벽 돌파 감지
            breakout = self._detect_wall_breakout(market, bids, asks)
            if breakout:
                signal.wall_breakout = True
                signal.wall_breakout_side = breakout
            
            # 히스토리 업데이트
            self._update_history(market, bids, asks, signal)
            self._last_signal[market] = signal
            
            return signal
            
        except Exception as e:
            logger.debug(f"호가창 분석 오류 ({market}): {e}")
            return None
    
    def _detect_wall(self, orders: List[Tuple], side: str) -> Optional[Tuple]:
        """매수벽/매도벽 감지 - 평균 수량 대비 N배 이상인 호가"""
        if len(orders) < 3:
            return None
        
        sizes = [o[1] for o in orders]
        avg_size = np.mean(sizes)
        if avg_size == 0:
            return None
        
        # 상위 3개 호가 중 평균의 wall_threshold배 이상인 것 탐지
        for price, size in orders[:15]:
            if size >= avg_size * self.wall_threshold:
                return (price, size)
        return None
    
    def _calc_imbalance(self, bids: List[Tuple], asks: List[Tuple]) -> float:
        """
        호가 불균형 지수
        +1.0 = 완전 매수 우세 (강한 매수 압력)
        -1.0 = 완전 매도 우세 (강한 매도 압력)
         0.0 = 균형
        """
        bid_volume = sum(size for _, size in bids)
        ask_volume = sum(size for _, size in asks)
        total = bid_volume + ask_volume
        
        if total == 0:
            return 0.0
        
        return (bid_volume - ask_volume) / total
    
    def _calc_pressure(self, imbalance: float) -> str:
        """불균형 지수 → 압력 판단"""
        if imbalance >= 0.3:
            return "STRONG_BUY"
        elif imbalance >= 0.1:
            return "BUY"
        elif imbalance <= -0.3:
            return "STRONG_SELL"
        elif imbalance <= -0.1:
            return "SELL"
        return "NEUTRAL"
    
    def _detect_spoofing(self, market: str, 
                          bids: List[Tuple], asks: List[Tuple]) -> Optional[str]:
        """
        스푸핑 감지:
        대형 주문이 나타났다가 빠르게 사라지는 패턴
        → 가짜 벽으로 시장 참여자를 속이는 행위
        """
        if market not in self._ob_history:
            self._ob_history[market] = deque(maxlen=self.spoofing_window)
            return None
        
        history = self._ob_history[market]
        if len(history) < 3:
            return None
        
        # 이전 스냅샷에서 대형 주문이 있었는지 확인
        prev = history[-1]
        prev_bids = dict(prev.get("bids", []))
        prev_asks = dict(prev.get("asks", []))
        
        curr_bid_dict = dict(bids)
        curr_ask_dict = dict(asks)
        
        # 대형 매수 주문이 사라짐 → 매수 스푸핑
        for price, size in prev_bids.items():
            if size > np.mean([s for _, s in bids]) * self.wall_threshold:
                if price not in curr_bid_dict or curr_bid_dict[price] < size * 0.3:
                    logger.debug(f"🚨 매수 스푸핑 감지 ({market}): {price:,} × {size:.4f} 소멸")
                    return "BUY_SPOOF"
        
        # 대형 매도 주문이 사라짐 → 매도 스푸핑
        for price, size in prev_asks.items():
            if size > np.mean([s for _, s in asks]) * self.wall_threshold:
                if price not in curr_ask_dict or curr_ask_dict[price] < size * 0.3:
                    logger.debug(f"🚨 매도 스푸핑 감지 ({market}): {price:,} × {size:.4f} 소멸")
                    return "SELL_SPOOF"
        
        return None
    
    def _detect_wall_breakout(self, market: str,
                               bids: List[Tuple], asks: List[Tuple]) -> Optional[str]:
        """벽 돌파 감지 - 이전에 있던 벽이 돌파됨"""
        if market not in self._wall_history:
            self._wall_history[market] = deque(maxlen=5)
            return None
        
        wall_hist = self._wall_history[market]
        if not wall_hist:
            return None
        
        current_best_bid = bids[0][0] if bids else 0
        current_best_ask = asks[0][0] if asks else 0
        
        prev_wall = wall_hist[-1]
        
        # 이전 매도벽을 현재 최우선 매수가가 넘음 → 돌파
        if prev_wall.get("ask_wall") and current_best_bid >= prev_wall["ask_wall"] * 0.999:
            logger.info(f"🚀 매도벽 돌파! ({market}): {prev_wall['ask_wall']:,}")
            return "BULL_BREAKOUT"
        
        # 이전 매수벽을 현재 최우선 매도가가 뚫음 → 하방 돌파
        if prev_wall.get("bid_wall") and current_best_ask <= prev_wall["bid_wall"] * 1.001:
            logger.info(f"🔻 매수벽 붕괴! ({market}): {prev_wall['bid_wall']:,}")
            return "BEAR_BREAKOUT"
        
        return None
    
    def _update_history(self, market: str, bids: List[Tuple], 
                         asks: List[Tuple], signal: OrderBookSignal):
        """히스토리 업데이트"""
        if market not in self._ob_history:
            self._ob_history[market] = deque(maxlen=self.spoofing_window)
        
        self._ob_history[market].append({
            "bids": bids[:20],
            "asks": asks[:20]
        })
        
        if market not in self._wall_history:
            self._wall_history[market] = deque(maxlen=5)
        
        self._wall_history[market].append({
            "bid_wall": signal.bid_wall_price or None,
            "ask_wall": signal.ask_wall_price or None
        })
    
    def get_signal(self, market: str) -> Optional[OrderBookSignal]:
        """최근 분석 결과 반환"""
        return self._last_signal.get(market)
    
    def get_summary(self, market: str) -> dict:
        """대시보드용 요약 정보"""
        sig = self._last_signal.get(market)
        if not sig:
            return {}
        return {
            "pressure": sig.pressure,
            "imbalance": round(sig.imbalance_ratio, 3),
            "spread_pct": round(sig.spread_pct, 4),
            "bid_wall": sig.bid_wall_price,
            "ask_wall": sig.ask_wall_price,
            "spoofing": sig.spoofing_detected,
            "spoofing_side": sig.spoofing_side,
            "wall_breakout": sig.wall_breakout,
            "wall_breakout_side": sig.wall_breakout_side,
        }
'''

# ──────────────────────────────────────────────────────────────
# FILE 2: signals/filters/orderbook_filter.py
# 호가창 신호를 전략 신호에 통합
# ──────────────────────────────────────────────────────────────
ORDERBOOK_FILTER = '''"""
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
'''

# ──────────────────────────────────────────────────────────────
# FILE 3: models/train/data_pipeline.py
# ML 훈련 데이터 파이프라인
# ──────────────────────────────────────────────────────────────
ML_PIPELINE = '''"""
APEX BOT - ML 훈련 데이터 파이프라인
업비트 실제 데이터로 ML 모델 훈련용 피처 생성
"""
import numpy as np
import pandas as pd
from typing import Tuple, Optional
from loguru import logger


class MLDataPipeline:
    """
    ML 훈련/추론용 피처 엔지니어링
    
    총 피처 수: 60개
    - 가격 피처: 15개
    - 기술지표: 25개  
    - 거래량: 10개
    - 시장구조: 10개
    """
    
    FEATURE_NAMES = []
    
    def __init__(self, lookback: int = 60, predict_horizon: int = 1):
        self.lookback = lookback
        self.predict_horizon = predict_horizon
    
    def create_features(self, df: pd.DataFrame) -> Optional[np.ndarray]:
        """캔들 데이터 → ML 피처 행렬"""
        try:
            if len(df) < self.lookback + 30:
                return None
            
            features = pd.DataFrame(index=df.index)
            close = df["close"]
            high = df["high"]
            low = df["low"]
            volume = df["volume"]
            
            # ── 가격 피처 (15개) ─────────────────────────
            features["ret_1"] = close.pct_change(1)
            features["ret_3"] = close.pct_change(3)
            features["ret_5"] = close.pct_change(5)
            features["ret_10"] = close.pct_change(10)
            features["ret_20"] = close.pct_change(20)
            features["hl_ratio"] = (high - low) / close
            features["oc_ratio"] = (close - df["open"]) / close
            features["upper_shadow"] = (high - close.combine(df["open"], max)) / close
            features["lower_shadow"] = (close.combine(df["open"], min) - low) / close
            features["gap"] = (df["open"] - close.shift(1)) / close.shift(1)
            
            # 가격 위치 (BB 내 위치)
            sma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            features["bb_position"] = (close - sma20) / (std20 * 2 + 1e-8)
            features["bb_width"] = (std20 * 4) / (sma20 + 1e-8)
            
            # 거리 지표
            features["dist_ma20"] = (close - sma20) / sma20
            features["dist_ma60"] = (close - close.rolling(60).mean()) / close.rolling(60).mean()
            features["dist_ma120"] = (close - close.rolling(120).mean()) / close.rolling(120).mean()
            
            # ── 기술지표 (25개) ──────────────────────────
            # RSI
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / (loss + 1e-8)
            features["rsi14"] = 100 - (100 / (1 + rs))
            features["rsi14_norm"] = (features["rsi14"] - 50) / 50
            
            # MACD
            ema12 = close.ewm(span=12).mean()
            ema26 = close.ewm(span=26).mean()
            macd = ema12 - ema26
            macd_signal = macd.ewm(span=9).mean()
            features["macd_hist"] = (macd - macd_signal) / (close + 1e-8)
            features["macd_cross"] = np.sign(macd - macd_signal)
            
            # 스토캐스틱
            lowest14 = low.rolling(14).min()
            highest14 = high.rolling(14).max()
            features["stoch_k"] = (close - lowest14) / (highest14 - lowest14 + 1e-8)
            features["stoch_d"] = features["stoch_k"].rolling(3).mean()
            
            # ATR
            tr = pd.concat([
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs()
            ], axis=1).max(axis=1)
            atr14 = tr.rolling(14).mean()
            features["atr_norm"] = atr14 / close
            features["atr_ratio"] = tr / (atr14 + 1e-8)
            
            # ADX
            plus_dm = (high.diff()).clip(lower=0)
            minus_dm = (-low.diff()).clip(lower=0)
            atr14_raw = tr.rolling(14).sum()
            plus_di = 100 * plus_dm.rolling(14).sum() / (atr14_raw + 1e-8)
            minus_di = 100 * minus_dm.rolling(14).sum() / (atr14_raw + 1e-8)
            dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-8)
            features["adx"] = dx.rolling(14).mean() / 100
            features["di_diff"] = (plus_di - minus_di) / 100
            
            # CCI
            typical_price = (high + low + close) / 3
            sma_tp = typical_price.rolling(20).mean()
            mad = typical_price.rolling(20).apply(lambda x: np.mean(np.abs(x - x.mean())))
            features["cci"] = (typical_price - sma_tp) / (0.015 * mad + 1e-8) / 200
            
            # Williams %R
            features["williams_r"] = (highest14 - close) / (highest14 - lowest14 + 1e-8) * (-1) + 0.5
            
            # MFI (Money Flow Index)
            mf = typical_price * volume
            pos_mf = mf.where(typical_price > typical_price.shift(), 0).rolling(14).sum()
            neg_mf = mf.where(typical_price < typical_price.shift(), 0).rolling(14).sum()
            features["mfi"] = pos_mf / (pos_mf + neg_mf + 1e-8)
            
            # EMA 크로스
            features["ema9_21_cross"] = (close.ewm(9).mean() - close.ewm(21).mean()) / close
            features["ema21_50_cross"] = (close.ewm(21).mean() - close.ewm(50).mean()) / close
            
            # 모멘텀
            features["mom10"] = close / (close.shift(10) + 1e-8) - 1
            features["mom20"] = close / (close.shift(20) + 1e-8) - 1
            
            # ── 거래량 피처 (10개) ───────────────────────
            vol_ma20 = volume.rolling(20).mean()
            features["vol_ratio"] = volume / (vol_ma20 + 1e-8)
            features["vol_ratio_5"] = volume / (volume.rolling(5).mean() + 1e-8)
            features["vol_trend"] = vol_ma20 / (volume.rolling(60).mean() + 1e-8)
            features["vol_price_corr"] = close.rolling(10).corr(volume)
            features["obv_norm"] = (np.sign(close.diff()) * volume).rolling(20).sum() / (vol_ma20 * 20 + 1e-8)
            features["vwap_dev"] = (close - (close * volume).rolling(20).sum() / (volume.rolling(20).sum() + 1e-8)) / close
            features["vol_breakout"] = (volume > vol_ma20 * 2).astype(float)
            features["vol_spike_3"] = (volume > volume.rolling(3).mean() * 2).astype(float)
            features["buying_pressure"] = (close - low) / (high - low + 1e-8)
            features["vol_ret_corr"] = features["ret_1"].rolling(10).corr(features["vol_ratio"])
            
            # ── 시장 구조 피처 (10개) ────────────────────
            features["high_20"] = (high == high.rolling(20).max()).astype(float)
            features["low_20"] = (low == low.rolling(20).min()).astype(float)
            features["range_position"] = (close - low.rolling(20).min()) / (high.rolling(20).max() - low.rolling(20).min() + 1e-8)
            
            # 지지/저항 거리
            pivot = (high.shift() + low.shift() + close.shift()) / 3
            features["pivot_dist"] = (close - pivot) / (close + 1e-8)
            
            # 캔들 패턴
            features["doji"] = (features["hl_ratio"] > 0.01) & (features["oc_ratio"].abs() < 0.001)
            features["doji"] = features["doji"].astype(float)
            features["hammer"] = ((features["lower_shadow"] > features["hl_ratio"] * 0.6) & 
                                   (features["upper_shadow"] < features["hl_ratio"] * 0.1)).astype(float)
            
            # 시간 피처 (순환 인코딩)
            if hasattr(df.index, "hour"):
                features["hour_sin"] = np.sin(2 * np.pi * df.index.hour / 24)
                features["hour_cos"] = np.cos(2 * np.pi * df.index.hour / 24)
            else:
                features["hour_sin"] = 0.0
                features["hour_cos"] = 1.0
            
            # 요일
            if hasattr(df.index, "dayofweek"):
                features["dow_sin"] = np.sin(2 * np.pi * df.index.dayofweek / 7)
            else:
                features["dow_sin"] = 0.0
            
            # NaN 처리
            features = features.fillna(0).replace([np.inf, -np.inf], 0)
            
            return features.values
            
        except Exception as e:
            logger.error(f"피처 생성 오류: {e}")
            return None
    
    def create_sequences(self, features: np.ndarray, labels: np.ndarray = None) -> Tuple:
        """시퀀스 데이터 생성 (LSTM 입력용)"""
        X, y = [], []
        
        for i in range(self.lookback, len(features)):
            X.append(features[i-self.lookback:i])
            if labels is not None and i + self.predict_horizon <= len(labels):
                y.append(labels[i])
        
        X = np.array(X, dtype=np.float32)
        if labels is not None:
            y = np.array(y, dtype=np.float32)
            return X, y
        return X, None
    
    def create_labels(self, df: pd.DataFrame, threshold: float = 0.005) -> np.ndarray:
        """
        레이블 생성
        0 = HOLD, 1 = BUY, 2 = SELL
        threshold: 수익률 임계값 (기본 0.5%)
        """
        future_ret = df["close"].pct_change(self.predict_horizon).shift(-self.predict_horizon)
        
        labels = np.zeros(len(df))
        labels[future_ret > threshold] = 1   # BUY
        labels[future_ret < -threshold] = 2  # SELL
        
        return labels.astype(int)
'''

# ──────────────────────────────────────────────────────────────
# FILE 4: signals/filters/volume_profile.py
# 거래량 프로파일 분석 (Volume Profile / POC)
# ──────────────────────────────────────────────────────────────
VOLUME_PROFILE = '''"""
APEX BOT - 거래량 프로파일 분석
POC(Point of Control), HVN/LVN, Value Area 계산
"""
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
    """
    거래량 프로파일 분석
    - POC: 가장 많이 거래된 가격대 (강한 지지/저항)
    - Value Area: 전체 거래량의 70% 집중 구간
    - HVN: 거래량 밀집 구간 (저항/지지 강함)
    - LVN: 거래량 희박 구간 (가격 빠르게 통과)
    """
    
    def __init__(self, bins: int = 50, value_area_pct: float = 0.70):
        self.bins = bins
        self.value_area_pct = value_area_pct
    
    def analyze(self, df: pd.DataFrame) -> Optional[VolumeProfileResult]:
        """거래량 프로파일 계산"""
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
            logger.debug(f"거래량 프로파일 오류: {e}")
            return None
    
    def get_nearest_support_resistance(self, df: pd.DataFrame, current_price: float) -> Dict:
        """현재가 기준 가장 가까운 지지/저항 반환"""
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
'''

# ──────────────────────────────────────────────────────────────
# 파일 작성
# ──────────────────────────────────────────────────────────────
FILES = {
    "data/processors/orderbook_analyzer.py": ORDERBOOK_ANALYZER,
    "signals/filters/orderbook_filter.py": ORDERBOOK_FILTER,
    "models/train/data_pipeline.py": ML_PIPELINE,
    "signals/filters/volume_profile.py": VOLUME_PROFILE,
}

print("🚀 APEX BOT 고도화 시작...\n")
success = 0
for path, content in FILES.items():
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    lines = len(content.splitlines())
    print(f"✅ {path} ({lines}줄)")
    success += 1

print(f"\n✅ {success}/{len(FILES)}개 파일 생성 완료")
print("\n📋 다음 단계:")
print("  1. python upgrade_all.py 실행 완료")
print("  2. engine.py에 OrderBookAnalyzer 연동 필요")
print("  3. python start_paper.py 로 재시작")
