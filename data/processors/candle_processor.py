"""
APEX BOT - 캔들 데이터 프로세서
원시 OHLCV → 기술지표 통합 + 멀티 타임프레임 정렬

수정 이력:
  v1.1 - _calc_supertrend() pandas CoW(Copy-on-Write) 경고 수정
         .iloc[i] = 직접 할당 → 명시적 .copy() + .at[] 방식으로 변경
       - 기타 iloc 할당 방어 처리
"""
import asyncio
from typing import Dict, Optional, List
import pandas as pd
import numpy as np
from loguru import logger

from config.settings import get_settings


class CandleProcessor:
    """
    멀티 타임프레임 캔들 데이터 전처리기
    - 업비트 OHLCV 데이터 정규화
    - 기술지표 일괄 계산
    - 타임프레임 동기화
    """

    TIMEFRAME_MAP = {
        "1": "minute1", "5": "minute5", "15": "minute15",
        "60": "minute60", "240": "minute240", "1440": "day",
    }

    def __init__(self):
        self.settings = get_settings()
        self._candle_cache: Dict[str, Dict[str, pd.DataFrame]] = {}
        self._lock = asyncio.Lock()

        try:
            import pandas_ta as ta
            self._ta = ta
            logger.info("✅ pandas_ta 로드 성공")
        except ImportError:
            self._ta = None
            logger.warning("⚠️ pandas_ta 미설치 - 기본 지표만 사용")

    async def process(
        self, market: str, raw_df: pd.DataFrame, timeframe: str = "60"
    ) -> Optional[pd.DataFrame]:
        """원시 캔들 → 지표 포함 DataFrame 반환"""
        if raw_df is None or raw_df.empty:
            return None
        try:
            df = self._normalize(raw_df.copy())
            df = self._add_indicators(df)
            df = self._add_volume_analysis(df)
            df = self._add_candle_patterns(df)
            df = df.dropna(subset=["ema20", "rsi"])

            async with self._lock:
                if market not in self._candle_cache:
                    self._candle_cache[market] = {}
                max_c = self.settings.database.cache_max_candles
                self._candle_cache[market][timeframe] = df.tail(max_c)

            return df
        except Exception as e:
            logger.error(f"캔들 처리 실패 ({market}/{timeframe}): {e}")
            return None

    def get_cached(
        self, market: str, timeframe: str = "60"
    ) -> Optional[pd.DataFrame]:
        return self._candle_cache.get(market, {}).get(timeframe)

    # ── 데이터 정규화 ─────────────────────────────────────────────

    def _normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in df.columns:
                raise ValueError(f"필수 컬럼 누락: {col}")
        df = df.sort_index()
        df = df.astype({
            "open": float, "high": float, "low": float,
            "close": float, "volume": float,
        })
        return df

    # ── 기술지표 계산 ─────────────────────────────────────────────

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

        # 이동평균
        for p in [5, 10, 20, 50, 100, 200]:
            df[f"ema{p}"] = c.ewm(span=p, adjust=False).mean()
            df[f"sma{p}"] = c.rolling(p).mean()

        # MACD
        e12 = c.ewm(span=12, adjust=False).mean()
        e26 = c.ewm(span=26, adjust=False).mean()
        df["macd"]        = e12 - e26
        df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
        df["macd_hist"]   = df["macd"] - df["macd_signal"]

        # RSI
        delta = c.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, np.nan)
        df["rsi"]      = 100 - (100 / (1 + rs))
        df["rsi_fast"] = self._calc_rsi(c, 7)
        df["rsi_slow"] = self._calc_rsi(c, 21)

        # 볼린저 밴드
        bb_mid = c.rolling(20).mean()
        bb_std = c.rolling(20).std()
        df["bb_upper"] = bb_mid + 2 * bb_std
        df["bb_mid"]   = bb_mid
        df["bb_lower"] = bb_mid - 2 * bb_std
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / (bb_mid + 1e-10)
        df["bb_pct"]   = (c - df["bb_lower"]) / (
            df["bb_upper"] - df["bb_lower"] + 1e-10
        )

        # ATR
        tr = pd.concat([
            h - l,
            (h - c.shift()).abs(),
            (l - c.shift()).abs(),
        ], axis=1).max(axis=1)
        df["atr"]     = tr.ewm(span=14, adjust=False).mean()
        df["atr_pct"] = df["atr"] / (c + 1e-10) * 100

        # Stochastic
        low14  = l.rolling(14).min()
        high14 = h.rolling(14).max()
        df["stoch_k"] = 100 * (c - low14) / (high14 - low14 + 1e-10)
        df["stoch_d"] = df["stoch_k"].rolling(3).mean()

        # VWAP
        tp        = (h + l + c) / 3
        df["vwap"] = (tp * v).cumsum() / v.cumsum()

        # ✅ FIX: CoW 경고 없는 슈퍼트렌드
        df["supertrend"], df["supertrend_dir"] = self._calc_supertrend(df)

        # OBV
        df["obv"]     = (np.sign(c.diff()) * v).fillna(0).cumsum()
        df["obv_ema"] = df["obv"].ewm(span=20, adjust=False).mean()

        # ADX
        df["adx"], df["di_plus"], df["di_minus"] = self._calc_adx(df)

        # CCI
        tp2       = (h + l + c) / 3
        df["cci"] = (tp2 - tp2.rolling(20).mean()) / (
            0.015 * tp2.rolling(20).std() + 1e-10
        )

        # Williams %R
        df["willr"] = -100 * (h.rolling(14).max() - c) / (
            h.rolling(14).max() - l.rolling(14).min() + 1e-10
        )

        # Ichimoku
        df["ich_tenkan"]   = (h.rolling(9).max()  + l.rolling(9).min())  / 2
        df["ich_kijun"]    = (h.rolling(26).max() + l.rolling(26).min()) / 2
        df["ich_senkou_a"] = ((df["ich_tenkan"] + df["ich_kijun"]) / 2).shift(26)
        df["ich_senkou_b"] = (
            (h.rolling(52).max() + l.rolling(52).min()) / 2
        ).shift(26)
        df["ich_chikou"]   = c.shift(-26)

        return df

    def _add_volume_analysis(self, df: pd.DataFrame) -> pd.DataFrame:
        v = df["volume"]
        df["vol_sma20"] = v.rolling(20).mean()
        df["vol_ratio"] = v / (df["vol_sma20"].replace(0, np.nan))
        df["vol_spike"] = df["vol_ratio"] > 2.0
        df["vol_trend"] = v.rolling(5).mean() > v.rolling(20).mean()

        tp     = (df["high"] + df["low"] + df["close"]) / 3
        mf     = tp * v
        pos_mf = mf.where(tp > tp.shift(), 0).rolling(14).sum()
        neg_mf = mf.where(tp < tp.shift(), 0).rolling(14).sum()
        df["mfi"] = 100 - (100 / (1 + pos_mf / (neg_mf.replace(0, np.nan))))
        return df

    def _add_candle_patterns(self, df: pd.DataFrame) -> pd.DataFrame:
        o, h, l, c = df["open"], df["high"], df["low"], df["close"]
        body        = abs(c - o)
        upper_wick  = h - c.where(c > o, o)
        lower_wick  = c.where(c < o, o) - l

        df["doji"]             = body < (h - l) * 0.1
        df["hammer"]           = (lower_wick > body * 2) & (upper_wick < body * 0.5)
        df["inverted_hammer"]  = (upper_wick > body * 2) & (lower_wick < body * 0.5)
        df["bullish"]          = c > o
        df["bearish"]          = c < o
        df["bull_engulf"]      = (
            df["bearish"].shift(1) & df["bullish"] &
            (o < c.shift(1)) & (c > o.shift(1))
        )
        df["squeeze"] = body < body.rolling(10).mean() * 0.5
        return df

    # ── 보조 지표 계산 ────────────────────────────────────────────

    @staticmethod
    def _calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain  = delta.clip(lower=0).rolling(period).mean()
        loss  = (-delta.clip(upper=0)).rolling(period).mean()
        rs    = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _calc_supertrend(
        df: pd.DataFrame, period: int = 10, multiplier: float = 3.0
    ):
        """
        ✅ FIX: pandas CoW 경고 없는 슈퍼트렌드 계산
        .iloc[i] = 직접 할당 제거 → numpy 배열 기반으로 재구현
        """
        atr = df["atr"]
        hl2 = (df["high"] + df["low"]) / 2

        upper_band = (hl2 + multiplier * atr).values
        lower_band = (hl2 - multiplier * atr).values
        close      = df["close"].values
        n          = len(close)

        supertrend = np.full(n, np.nan)
        direction  = np.ones(n)   # 1 = 상승, -1 = 하락

        # 첫 번째 유효 인덱스 초기화
        first_valid = atr.first_valid_index()
        start = df.index.get_loc(first_valid) if first_valid is not None else 1

        for i in range(start, n):
            prev_st  = supertrend[i - 1] if i > 0 and not np.isnan(supertrend[i - 1]) else lower_band[i]
            prev_dir = direction[i - 1] if i > 0 else 1

            if close[i] > prev_st:
                direction[i]  = 1
                supertrend[i] = max(lower_band[i], prev_st)
            else:
                direction[i]  = -1
                supertrend[i] = min(upper_band[i], prev_st)

        st_series  = pd.Series(supertrend, index=df.index)
        dir_series = pd.Series(direction,  index=df.index)
        return st_series, dir_series

    @staticmethod
    def _calc_adx(df: pd.DataFrame, period: int = 14):
        h, l, c = df["high"], df["low"], df["close"]
        tr = pd.concat([
            h - l,
            (h - c.shift()).abs(),
            (l - c.shift()).abs(),
        ], axis=1).max(axis=1)

        dm_plus  = (h - h.shift()).clip(lower=0).where(
            (h - h.shift()) > (l.shift() - l), 0
        )
        dm_minus = (l.shift() - l).clip(lower=0).where(
            (l.shift() - l) > (h - h.shift()), 0
        )

        atr14    = tr.ewm(span=period, adjust=False).mean()
        di_plus  = 100 * dm_plus.ewm(span=period, adjust=False).mean() / (atr14 + 1e-10)
        di_minus = 100 * dm_minus.ewm(span=period, adjust=False).mean() / (atr14 + 1e-10)
        dx       = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus + 1e-10)
        adx_val  = dx.ewm(span=period, adjust=False).mean()
        return adx_val, di_plus, di_minus

    # ── 멀티 타임프레임 ────────────────────────────────────────────

    def get_multi_tf_signal(self, market: str) -> Dict:
        signals = {}
        for tf in ["5", "60", "1440"]:
            df = self.get_cached(market, tf)
            if df is not None and not df.empty:
                last = df.iloc[-1]
                signals[tf] = {
                    "trend":       "UP" if last["close"] > last["ema200"] else "DOWN",
                    "rsi":         last["rsi"],
                    "macd_bull":   last["macd"] > last["macd_signal"],
                    "supertrend":  "UP" if last["supertrend_dir"] > 0 else "DOWN",
                }
        return signals
