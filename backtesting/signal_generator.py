"""
APEX BOT Backtester - 신호 생성기
백테스트용 기술지표 기반 신호 생성 (전략 8개 대응)
각 전략은 OHLCV DataFrame을 받아 pd.Series(+1/-1/0)을 반환합니다.
"""
import numpy as np
import pandas as pd
from typing import Callable, Dict
from loguru import logger


# ── 보조 지표 함수들 ──────────────────────────────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl  = df["high"] - df["low"]
    hc  = (df["high"] - df["close"].shift()).abs()
    lc  = (df["low"]  - df["close"].shift()).abs()
    tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def _macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd     = ema_fast - ema_slow
    sig      = _ema(macd, signal)
    return macd, sig

def _bollinger(close: pd.Series, period=20, std_mult=2.0):
    mid  = close.rolling(period).mean()
    std  = close.rolling(period).std()
    return mid + std_mult * std, mid, mid - std_mult * std

def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ADX 근사 계산"""
    atr_val = _atr(df, period)
    up_move   = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm   = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm  = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_di   = 100 * pd.Series(plus_dm,  index=df.index).rolling(period).mean() / atr_val.replace(0, np.nan)
    minus_di  = 100 * pd.Series(minus_dm, index=df.index).rolling(period).mean() / atr_val.replace(0, np.nan)
    dx        = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    return dx.rolling(period).mean()


# ── 전략 신호 생성 함수들 ─────────────────────────────────────────────────

def signal_volatility_breakout(df: pd.DataFrame, k: float = 0.5) -> pd.Series:
    """변동성 돌파 전략 (Larry Williams) - Look-Ahead Bias 없음 확인"""
    prev_range = (df["high"] - df["low"]).shift(1)   # 전일 범위 (과거)
    target     = df["open"] + prev_range * k          # 당일 목표가
    sig = pd.Series(0, index=df.index)
    sig[df["close"] > target] = 1
    sig = sig.shift(1).fillna(0)                      # 다음 봉 진입
    return sig.astype(int)


def signal_mean_reversion(df: pd.DataFrame, period: int = 20, z_thresh: float = 2.0) -> pd.Series:
    """평균회귀 전략 (Bollinger Band Z-score)"""
    bb_upper, bb_mid, bb_lower = _bollinger(df["close"], period)
    z = (df["close"] - bb_mid) / (df["close"].rolling(period).std().replace(0, np.nan))
    sig = pd.Series(0, index=df.index)
    sig[z < -z_thresh]  = 1
    sig[z >  z_thresh]  = -1
    return sig.shift(1).fillna(0).astype(int)  # 다음 봉 진입


def signal_trend_following(df: pd.DataFrame, fast: int = 50, slow: int = 200) -> pd.Series:
    """추세 추종 (EMA 크로스오버)"""
    ema_fast = _ema(df["close"], fast)
    ema_slow = _ema(df["close"], slow)
    sig = pd.Series(0, index=df.index)
    sig[ema_fast > ema_slow] = 1
    sig[ema_fast < ema_slow] = -1
    # 크로스 시점만 신호
    sig_cross = sig.diff().fillna(0)
    result = pd.Series(0, index=df.index)
    result[sig_cross > 0] = 1
    result[sig_cross < 0] = -1
    return result.astype(int)


def signal_rsi_divergence(df: pd.DataFrame, rsi_period: int = 14,
                          oversold: float = 30, overbought: float = 70) -> pd.Series:
    """RSI 다이버전스 전략"""
    rsi = _rsi(df["close"], rsi_period)
    sig = pd.Series(0, index=df.index)
    sig[rsi < oversold]  = 1
    sig[rsi > overbought] = -1
    return sig.shift(1).fillna(0).astype(int)  # 다음 봉 진입


def signal_macd_momentum(df: pd.DataFrame) -> pd.Series:
    """MACD 모멘텀 전략"""
    macd, macd_sig = _macd(df["close"])
    hist = macd - macd_sig
    sig = pd.Series(0, index=df.index)
    # 히스토그램 양전환 = BUY, 음전환 = SELL
    sig[hist > 0] = 1
    sig[hist < 0] = -1
    cross = sig.diff().fillna(0)
    result = pd.Series(0, index=df.index)
    result[cross > 0] = 1
    result[cross < 0] = -1
    return result.astype(int)


def signal_volume_spike(df: pd.DataFrame, vol_mult: float = 2.0,
                        period: int = 20) -> pd.Series:
    """거래량 급증 전략"""
    avg_vol = df["volume"].rolling(period).mean()
    spike   = df["volume"] > avg_vol * vol_mult
    sig = pd.Series(0, index=df.index)
    # 거래량 급증 + 가격 상승 = BUY
    sig[(spike) & (df["close"] > df["open"])]  = 1
    sig[(spike) & (df["close"] < df["open"])]  = -1
    return sig.shift(1).fillna(0).astype(int)  # 다음 봉 진입


def signal_order_block_smc(df: pd.DataFrame, swing_period: int = 5) -> pd.Series:
    """SMC 오더 블록 전략 (Look-Ahead Bias 수정: center=False + shift(1))"""
    # center=False: 과거 데이터만 참조
    # shift(1): 신호 발생 다음 봉에 진입 (현실적 실행)
    local_high = df["high"].rolling(swing_period * 2 + 1, center=False).max().shift(1)
    local_low  = df["low"].rolling(swing_period * 2 + 1, center=False).min().shift(1)
    sig = pd.Series(0, index=df.index)
    near_low  = df["close"] <= local_low * 1.02
    near_high = df["close"] >= local_high * 0.98
    sig[near_low]  = 1
    sig[near_high] = -1
    return sig.astype(int)


def signal_ml_strategy(df: pd.DataFrame) -> pd.Series:
    """
    ML 전략 백테스트용 앙상블 근사 신호
    실제 모델 대신 다중 지표 조합으로 근사합니다.
    """
    rsi   = _rsi(df["close"], 14)
    macd_, macd_sig_ = _macd(df["close"])
    adx   = _adx(df, 14)
    ema50 = _ema(df["close"], 50)
    ema200 = _ema(df["close"], 200)

    score = pd.Series(0.0, index=df.index)
    score += (rsi < 35).astype(float) * 1.0       # 과매도
    score += (rsi > 65).astype(float) * -1.0      # 과매수
    score += ((macd_ > macd_sig_) & (adx > 20)).astype(float) * 1.0
    score += ((macd_ < macd_sig_) & (adx > 20)).astype(float) * -1.0
    score += (df["close"] > ema200).astype(float) * 0.5
    score += (df["close"] < ema200).astype(float) * -0.5

    sig = pd.Series(0, index=df.index)
    sig[score >= 1.5]  = 1
    sig[score <= -1.5] = -1
    return sig.astype(int)


# ── 전략 레지스트리 ────────────────────────────────────────────────────────

STRATEGIES: Dict[str, Callable] = {
    "volatility_breakout": signal_volatility_breakout,
    "mean_reversion":      signal_mean_reversion,
    "trend_following":     signal_trend_following,
    "rsi_divergence":      signal_rsi_divergence,
    "macd_momentum":       signal_macd_momentum,
    "volume_spike":        signal_volume_spike,
    "order_block_smc":     signal_order_block_smc,
    "ml_strategy":         signal_ml_strategy,
}


def get_signals(strategy_name: str, df: pd.DataFrame, **kwargs) -> pd.Series:
    """전략 이름으로 신호 생성"""
    if strategy_name not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy_name}. 가능: {list(STRATEGIES)}")
    fn = STRATEGIES[strategy_name]
    try:
        return fn(df, **kwargs)
    except Exception as e:
        logger.error(f"[{strategy_name}] 신호 생성 실패: {e}")
        return pd.Series(0, index=df.index)
