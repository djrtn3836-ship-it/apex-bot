"""APEX BOT -  
pandas   Python  (TA-Lib  )
GPU   (cupy )"""
import numpy as np
import pandas as pd
from typing import Tuple, Optional


# ── 이동평균 ──────────────────────────────────────────────────────
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def wma(series: pd.Series, period: int) -> pd.Series:
    weights = np.arange(1, period + 1)
    return series.rolling(period).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)


def hull_ma(series: pd.Series, period: int) -> pd.Series:
    """Hull Moving Average ( )"""
    half = period // 2
    sqrt_p = int(np.sqrt(period))
    return wma(2 * wma(series, half) - wma(series, period), sqrt_p)


# ── RSI ───────────────────────────────────────────────────────────
def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def stoch_rsi(series: pd.Series, rsi_period: int = 14,
              stoch_period: int = 14) -> Tuple[pd.Series, pd.Series]:
    """Stochastic RSI"""
    r = rsi(series, rsi_period)
    low = r.rolling(stoch_period).min()
    high = r.rolling(stoch_period).max()
    k = 100 * (r - low) / (high - low + 1e-10)
    d = k.rolling(3).mean()
    return k, d


# ── MACD ──────────────────────────────────────────────────────────
def macd(series: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ── 볼린저 밴드 ───────────────────────────────────────────────────
def bollinger_bands(series: pd.Series, period: int = 20,
                    std_dev: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    mid = sma(series, period)
    std = series.rolling(period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return upper, mid, lower


def bb_width(series: pd.Series, period: int = 20) -> pd.Series:
    upper, mid, lower = bollinger_bands(series, period)
    return (upper - lower) / mid


def bb_percent(series: pd.Series, period: int = 20) -> pd.Series:
    upper, mid, lower = bollinger_bands(series, period)
    return (series - lower) / (upper - lower + 1e-10)


# ── ATR ───────────────────────────────────────────────────────────
def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


# ── ADX ───────────────────────────────────────────────────────────
def adx(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    tr = atr(high, low, close, period)
    dm_plus = (high - high.shift()).clip(lower=0)
    dm_minus = (low.shift() - low).clip(lower=0)
    dm_plus = dm_plus.where(dm_plus > dm_minus, 0)
    dm_minus = dm_minus.where(dm_minus > dm_plus, 0)

    di_plus = 100 * dm_plus.ewm(span=period, adjust=False).mean() / (tr + 1e-10)
    di_minus = 100 * dm_minus.ewm(span=period, adjust=False).mean() / (tr + 1e-10)
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus + 1e-10)
    adx_line = dx.ewm(span=period, adjust=False).mean()
    return adx_line, di_plus, di_minus


# ── 켈트너 채널 ───────────────────────────────────────────────────
def keltner_channel(high: pd.Series, low: pd.Series, close: pd.Series,
                    ema_period: int = 20,
                    atr_mult: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    mid = ema(close, ema_period)
    atr_val = atr(high, low, close)
    return mid + atr_mult * atr_val, mid, mid - atr_mult * atr_val


# ── 슈퍼트렌드 ────────────────────────────────────────────────────
def supertrend(high: pd.Series, low: pd.Series, close: pd.Series,
               period: int = 10, multiplier: float = 3.0) -> Tuple[pd.Series, pd.Series]:
    atr_val = atr(high, low, close, period)
    hl2 = (high + low) / 2
    upper_band = hl2 + multiplier * atr_val
    lower_band = hl2 - multiplier * atr_val

    st = pd.Series(index=close.index, dtype=float)
    direction = pd.Series(1, index=close.index)

    for i in range(1, len(close)):
        if close.iloc[i] > st.iloc[i-1] if not pd.isna(st.iloc[i-1]) else upper_band.iloc[i]:
            direction.iloc[i] = 1
            st.iloc[i] = max(lower_band.iloc[i],
                              st.iloc[i-1] if not pd.isna(st.iloc[i-1]) else lower_band.iloc[i])
        else:
            direction.iloc[i] = -1
            st.iloc[i] = min(upper_band.iloc[i],
                              st.iloc[i-1] if not pd.isna(st.iloc[i-1]) else upper_band.iloc[i])

    return st, direction


# ── VWAP ─────────────────────────────────────────────────────────
def vwap(high: pd.Series, low: pd.Series, close: pd.Series,
         volume: pd.Series) -> pd.Series:
    tp = (high + low + close) / 3
    return (tp * volume).cumsum() / volume.cumsum()


# ── OBV ──────────────────────────────────────────────────────────
def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    return (np.sign(close.diff()) * volume).fillna(0).cumsum()


# ── CCI ──────────────────────────────────────────────────────────
def cci(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 20) -> pd.Series:
    tp = (high + low + close) / 3
    return (tp - tp.rolling(period).mean()) / (0.015 * tp.rolling(period).std() + 1e-10)


# ── MFI ──────────────────────────────────────────────────────────
def mfi(high: pd.Series, low: pd.Series, close: pd.Series,
        volume: pd.Series, period: int = 14) -> pd.Series:
    tp = (high + low + close) / 3
    mf = tp * volume
    pos_mf = mf.where(tp > tp.shift(), 0).rolling(period).sum()
    neg_mf = mf.where(tp < tp.shift(), 0).rolling(period).sum()
    return 100 - 100 / (1 + pos_mf / (neg_mf + 1e-10))


# ── 이치모쿠 ──────────────────────────────────────────────────────
def ichimoku(high: pd.Series, low: pd.Series) -> dict:
    tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
    kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(26)
    senkou_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
    return {
        "tenkan": tenkan, "kijun": kijun,
        "senkou_a": senkou_a, "senkou_b": senkou_b,
    }


# ── 허스트 지수 ───────────────────────────────────────────────────
def hurst_exponent(series: pd.Series, max_lag: int = 20) -> float:
    """(H>0.5: , H<0.5: )"""
    lags = range(2, min(max_lag, len(series) // 2))
    log_returns = np.log(series / series.shift(1)).dropna().values
    rs_vals = []
    for lag in lags:
        chunks = [log_returns[i:i+lag] for i in range(0, len(log_returns)-lag+1, lag)]
        rs = [(np.max(np.cumsum(c - c.mean())) - np.min(np.cumsum(c - c.mean()))) /
              (np.std(c) + 1e-10) for c in chunks if len(c) == lag]
        if rs:
            rs_vals.append(np.mean(rs))
    if len(rs_vals) < 2:
        return 0.5
    try:
        log_lags = np.log(list(lags)[:len(rs_vals)])
        return float(np.clip(np.polyfit(log_lags, np.log(rs_vals), 1)[0], 0, 1))
    except Exception:
        return 0.5


# ── 일괄 지표 계산 ────────────────────────────────────────────────
def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """DataFrame"""
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    # 이동평균
    for p in [5, 10, 20, 50, 100, 200]:
        df[f"ema{p}"] = ema(c, p)
        df[f"sma{p}"] = sma(c, p)

    # MACD
    df["macd"], df["macd_signal"], df["macd_hist"] = macd(c)

    # RSI
    df["rsi"] = rsi(c, 14)
    df["rsi_fast"] = rsi(c, 7)

    # 볼린저 밴드
    df["bb_upper"], df["bb_mid"], df["bb_lower"] = bollinger_bands(c)
    df["bb_width"] = bb_width(c)
    df["bb_pct"] = bb_percent(c)

    # ATR / ADX
    df["atr"] = atr(h, l, c)
    df["atr_pct"] = df["atr"] / c * 100
    df["adx"], df["di_plus"], df["di_minus"] = adx(h, l, c)

    # 볼륨
    df["vol_sma20"] = sma(v, 20)
    df["vol_ratio"] = v / (df["vol_sma20"] + 1e-10)

    # VWAP / OBV / CCI / MFI
    df["vwap"] = vwap(h, l, c, v)
    df["obv"] = obv(c, v)
    df["cci"] = cci(h, l, c)
    df["mfi"] = mfi(h, l, c, v)

    # Stochastic
    stoch_h = h.rolling(14).max()
    stoch_l = l.rolling(14).min()
    df["stoch_k"] = 100 * (c - stoch_l) / (stoch_h - stoch_l + 1e-10)
    df["stoch_d"] = sma(df["stoch_k"], 3)

    # 슈퍼트렌드
    df["supertrend"], df["supertrend_dir"] = supertrend(h, l, c)

    # 캔들 패턴
    df["bullish"] = c > df["open"]
    df["bearish"] = c < df["open"]

    return df


class TechnicalIndicators:
    """( )"""
    ema = staticmethod(ema)
    sma = staticmethod(sma)
    rsi = staticmethod(rsi)
    macd = staticmethod(macd)
    bollinger_bands = staticmethod(bollinger_bands)
    bb_width = staticmethod(bb_width)
    bb_percent = staticmethod(bb_percent)
    atr = staticmethod(atr)
    vwap = staticmethod(vwap)
    adx = staticmethod(adx)
    obv = staticmethod(obv)
    stoch_rsi = staticmethod(stoch_rsi)
    hull_ma = staticmethod(hull_ma)
    wma = staticmethod(wma)
