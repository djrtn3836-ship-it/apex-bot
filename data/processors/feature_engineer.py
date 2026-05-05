"""APEX BOT - ML  
120   ML   
RTX 5060 GPU"""
import numpy as np
import pandas as pd
from typing import Tuple, List, Optional
from sklearn.preprocessing import RobustScaler
import logging

logger = logging.getLogger(__name__)


class FeatureEngineer:
    """ML    
     120  
    RobustScaler"""

    def __init__(self):
        self.scaler = RobustScaler()
        self._fitted = False
        self._feature_names: List[str] = []

    def extract_features(
        self,
        df: pd.DataFrame,
        fit_scaler: bool = True,
    ) -> Tuple[Optional[np.ndarray], List[str]]:
        """DataFrame  
        
        Returns:
            (features_array, feature_names)
            features_array: (n_samples, n_features)"""
        try:
            features_df = self._build_features(df)
            features_df = features_df.dropna()

            if features_df.empty:
                return None, []

            feature_names = list(features_df.columns)
            self._feature_names = feature_names

            # 스케일링
            if fit_scaler:
                scaled = self.scaler.fit_transform(features_df.values)
                self._fitted = True
            else:
                if not self._fitted:
                    raise ValueError("  . fit_scaler=True  .")
                scaled = self.scaler.transform(features_df.values)

            # NaN/Inf 정리
            scaled = np.nan_to_num(scaled, nan=0.0, posinf=3.0, neginf=-3.0)
            scaled = np.clip(scaled, -10.0, 10.0)  # 이상치 클리핑

            logger.info(f"   : {scaled.shape[0]} × {scaled.shape[1]}")
            return scaled, feature_names

        except Exception as e:
            logger.error(f"   : {e}")
            return None, []

    def _build_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """(120  )"""
        feat = pd.DataFrame(index=df.index)
        c = df["close"]
        h = df["high"]
        l = df["low"]
        o = df["open"]
        v = df["volume"]

        # ─ 1. 가격 수익률 피처 (15개) ─
        for lag in [1, 2, 3, 5, 10, 15, 20]:
            feat[f"return_{lag}"] = c.pct_change(lag)
        for lag in [1, 2, 3]:
            feat[f"log_return_{lag}"] = np.log(c / c.shift(lag))
        feat["hl_ratio"] = (h - l) / c          # 고저 범위 비율
        feat["oc_ratio"] = (c - o) / o           # 시가대비 종가
        feat["upper_shadow"] = (h - c.clip(lower=o)) / c  # 위꼬리
        feat["lower_shadow"] = (o.clip(upper=c) - l) / c  # 아래꼬리
        feat["body_size"] = abs(c - o) / c       # 몸통 크기

        # ─ 2. 이동평균 관련 피처 (20개) ─
        for period in [5, 10, 20, 50, 100, 200]:
            # [FE-1 FIX] candle_processor는 ema{p} (언더스코어 없음) 로 저장
            if f"ema{period}" in df.columns:
                feat[f"ema{period}_dist"] = (c - df[f"ema{period}"]) / c
            if f"sma{period}" in df.columns:
                feat[f"sma{period}_dist"] = (c - df[f"sma{period}"]) / c
        # EMA 간 거리
        for fast, slow in [(5, 20), (20, 50), (50, 200)]:
            if f"ema_{fast}" in df.columns and f"ema_{slow}" in df.columns:
                feat[f"ema_{fast}_{slow}_spread"] = (df[f"ema_{fast}"] - df[f"ema_{slow}"]) / c
            if f"sma_{fast}" in df.columns and f"sma_{slow}" in df.columns:
                feat[f"sma_{fast}_{slow}_spread"] = (df[f"sma_{fast}"] - df[f"sma_{slow}"]) / c

        # ─ 3. 모멘텀 지표 (20개) ─
        for period in [7, 14, 21]:
            if f"rsi_{period}" in df.columns:
                feat[f"rsi_{period}"] = df[f"rsi_{period}"] / 100
                feat[f"rsi_{period}_diff"] = df[f"rsi_{period}"].diff(1) / 100

        if "macd" in df.columns:
            feat["macd_norm"] = df["macd"] / c
            feat["macd_signal_norm"] = df["macd_signal"] / c
            feat["macd_hist_norm"] = df["macd_hist"] / c
            feat["macd_hist_diff"] = df["macd_hist"].diff(1) / c

        if "stoch_k" in df.columns:
            feat["stoch_k"] = df["stoch_k"] / 100
            feat["stoch_d"] = df["stoch_d"] / 100
            feat["stoch_kd_diff"] = (df["stoch_k"] - df["stoch_d"]) / 100

        if "cci" in df.columns:
            feat["cci_norm"] = df["cci"] / 200    # CCI 정규화

        if "williams_r" in df.columns:
            feat["williams_r_norm"] = (df["williams_r"] + 100) / 100

        for period in [1, 5, 10, 20]:
            if f"roc_{period}" in df.columns:
                feat[f"roc_{period}_norm"] = df[f"roc_{period}"] / 100

        # ─ 4. 변동성 지표 (15개) ─
        for period in [7, 14, 21]:
            if f"atr_{period}" in df.columns:
                feat[f"atr_{period}_norm"] = df[f"atr_{period}"] / c

        if "bb_width" in df.columns:
            feat["bb_width"] = df["bb_width"]
        if "bb_pct" in df.columns:
            feat["bb_pct"] = df["bb_pct"].clip(0, 1)

        if "squeeze_on" in df.columns:
            feat["squeeze_on"] = df["squeeze_on"].astype(float)
        if "squeeze_mom" in df.columns:
            feat["squeeze_mom_norm"] = df["squeeze_mom"] / c

        if "volatility_20" in df.columns:
            feat["volatility_20"] = df["volatility_20"].clip(0, 1)

        # 롤링 변동성
        for window in [5, 10, 20]:
            feat[f"rolling_vol_{window}"] = c.pct_change().rolling(window).std()

        # ─ 5. 거래량 피처 (10개) ─
        if "volume_ratio" in df.columns:
            feat["volume_ratio"] = df["volume_ratio"].clip(0, 5) / 5
        if "obv" in df.columns:
            feat["obv_trend"] = df["obv"].pct_change(20).clip(-1, 1)
        if "obv_ema" in df.columns and "obv" in df.columns:
            feat["obv_ema_dist"] = (df["obv"] - df["obv_ema"]) / (df["obv_ema"].abs() + 1)

        for window in [5, 10, 20]:
            feat[f"vol_sma_{window}_ratio"] = v / (v.rolling(window).mean() + 1e-8)

        feat["vol_trend"] = v.pct_change(10).clip(-2, 2)

        # ─ 6. 시장 구조 피처 (10개) ─
        if "supertrend" in df.columns:
            feat["supertrend_dist"] = (c - df["supertrend"]) / c
        if "supertrend_dir" in df.columns:
            feat["supertrend_dir"] = df["supertrend_dir"]

        if "vwap" in df.columns:
            feat["vwap_dist"] = (c - df["vwap"]) / c

        # 지지/저항 거리
        for period in [20, 50]:
            feat[f"high_{period}_dist"] = (h.rolling(period).max() - c) / c
            feat[f"low_{period}_dist"] = (c - l.rolling(period).min()) / c

        # ─ 7. 시간 특성 피처 (10개) ─
        if "datetime" in df.columns:
            dt = pd.to_datetime(df["datetime"])
            feat["hour_sin"] = np.sin(2 * np.pi * dt.dt.hour / 24)
            feat["hour_cos"] = np.cos(2 * np.pi * dt.dt.hour / 24)
            feat["day_sin"] = np.sin(2 * np.pi * dt.dt.dayofweek / 7)
            feat["day_cos"] = np.cos(2 * np.pi * dt.dt.dayofweek / 7)
            feat["month_sin"] = np.sin(2 * np.pi * dt.dt.month / 12)
            feat["month_cos"] = np.cos(2 * np.pi * dt.dt.month / 12)
            feat["is_weekend"] = (dt.dt.dayofweek >= 5).astype(float)

        # ─ 8. 자기상관 / 통계 피처 (10개) ─
        for lag in [1, 5, 10, 20]:
            feat[f"autocorr_{lag}"] = c.pct_change().rolling(20).apply(
                lambda x: x.autocorr(lag=min(lag, len(x)-1)) if len(x) > lag else 0,
                raw=False
            ).fillna(0)

        feat["skewness_20"] = c.pct_change().rolling(20).skew().fillna(0)
        feat["kurtosis_20"] = c.pct_change().rolling(20).kurt().fillna(0).clip(-5, 5)

        # 최종 정리
        feat = feat.replace([np.inf, -np.inf], np.nan)

        logger.debug(f" : {len(feat.columns)}개")
        return feat

    def transform(self, df: pd.DataFrame) -> Optional[np.ndarray]:
        """(  )"""
        features_df = self._build_features(df)
        features_df = features_df.dropna()

        if features_df.empty or not self._fitted:
            return None

        # 피처 순서 맞추기
        missing = [f for f in self._feature_names if f not in features_df.columns]
        for col in missing:
            features_df[col] = 0.0

        features_df = features_df[self._feature_names]
        scaled = self.scaler.transform(features_df.values)
        scaled = np.nan_to_num(scaled, nan=0.0, posinf=3.0, neginf=-3.0)
        return np.clip(scaled, -10.0, 10.0)

    def get_latest_features(self, df: pd.DataFrame, sequence_length: int = 60) -> Optional[np.ndarray]:
        """get_latest_features 실행"""
        features = self.transform(df)
        if features is None or len(features) < sequence_length:
            return None
        return features[-sequence_length:][np.newaxis, :]  # (1, seq_len, n_features)
