"""
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
