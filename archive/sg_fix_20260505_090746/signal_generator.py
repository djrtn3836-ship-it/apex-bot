"""APEX BOT Backtester -  
     ( 8 )
  OHLCV DataFrame  pd.Series(+1/-1/0) ."""
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
    """ADX"""
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
    """(Larry Williams) - Look-Ahead Bias"""
    prev_range = (df["high"] - df["low"]).shift(1)   # 전일 범위 (과거)
    target     = df["open"] + prev_range * k          # 당일 목표가
    sig = pd.Series(0, index=df.index)
    sig[df["close"] > target] = 1
    sig = sig.shift(1).fillna(0)                      # 다음 봉 진입
    return sig.astype(int)


def signal_mean_reversion(df: pd.DataFrame, period: int = 20, z_thresh: float = 2.0) -> pd.Series:
    """(Bollinger Band Z-score)"""
    bb_upper, bb_mid, bb_lower = _bollinger(df["close"], period)
    z = (df["close"] - bb_mid) / (df["close"].rolling(period).std().replace(0, np.nan))
    sig = pd.Series(0, index=df.index)
    sig[z < -z_thresh]  = 1
    sig[z >  z_thresh]  = -1
    return sig.shift(1).fillna(0).astype(int)  # 다음 봉 진입


def signal_trend_following(df: pd.DataFrame, fast: int = 50, slow: int = 200) -> pd.Series:
    """(EMA )"""
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
    """RSI"""
    rsi = _rsi(df["close"], rsi_period)
    sig = pd.Series(0, index=df.index)
    sig[rsi < oversold]  = 1
    sig[rsi > overbought] = -1
    return sig.shift(1).fillna(0).astype(int)  # 다음 봉 진입


def signal_macd_momentum(df: pd.DataFrame) -> pd.Series:
    """MACD"""
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
    """signal_volume_spike 실행"""
    avg_vol = df["volume"].rolling(period).mean()
    spike   = df["volume"] > avg_vol * vol_mult
    sig = pd.Series(0, index=df.index)
    # 거래량 급증 + 가격 상승 = BUY
    sig[(spike) & (df["close"] > df["open"])]  = 1
    sig[(spike) & (df["close"] < df["open"])]  = -1
    return sig.shift(1).fillna(0).astype(int)  # 다음 봉 진입


def signal_order_block_smc(df: pd.DataFrame, swing_period: int = 5) -> pd.Series:
    """BUG-6 FIX: 실제 OrderBlockStrategy 로직과 일치
    - 캔들 바디 비율 확인 (body_ratio > 0.6)
    - 볼륨 확인 (평균 대비 0.3배 이상)
    - EMA50 추세 필터 (BUY: EMA50 위, SELL: EMA50 아래)
    - 근접도 필터 (touch_pct 0.5% 이내)
    """
    lb         = swing_period
    body_ratio = 0.6
    touch_pct  = 0.005
    ema50      = df["close"].ewm(span=50, adjust=False).mean()
    avg_vol    = df["volume"].rolling(20).mean() if "volume" in df.columns else None
    sig        = pd.Series(0, index=df.index)

    for i in range(lb * 2 + 1, len(df)):
        recent     = df.iloc[i - lb: i]
        price      = float(df["close"].iloc[i])
        ema50_val  = float(ema50.iloc[i])

        # 볼륨 필터
        if avg_vol is not None:
            vol_r = float(df["volume"].iloc[i]) / (float(avg_vol.iloc[i]) + 1e-9)
            if vol_r < 0.3:
                continue

        body = (recent["close"] - recent["open"]).abs()
        rng  = (recent["high"]  - recent["low"]).abs() + 1e-9
        ratio = body / rng

        # 불리시 OB: 강한 양봉 + 현재가 저점 근처 + EMA50 위
        bull_ob = recent[(recent["close"] > recent["open"]) & (ratio > body_ratio)]
        if not bull_ob.empty and price > ema50_val:
            ob_low     = float(bull_ob["low"].iloc[-1])
            touch_dist = abs(price - ob_low) / (ob_low + 1e-9)
            if touch_dist < touch_pct:
                sig.iloc[i] = 1
                continue

        # 베어리시 OB: 강한 음봉 + 현재가 고점 근처 + EMA50 아래
        bear_ob = recent[(recent["close"] < recent["open"]) & (ratio > body_ratio)]
        if not bear_ob.empty and price < ema50_val:
            ob_high    = float(bear_ob["high"].iloc[-1])
            touch_dist = abs(price - ob_high) / (ob_high + 1e-9)
            if touch_dist < touch_pct:
                sig.iloc[i] = -1

    return sig.astype(int)


def signal_ml_strategy(df: pd.DataFrame) -> pd.Series:
    """실제 ensemble_best.pt 모델 기반 ML 전략
    - 입력: 120개 피처, 시퀀스 60봉
    - 출력: 수익률 예측값 (회귀)
    - buy_thr=+0.333333 (상위20%), sell_thr=+0.333333 (하위20%)
    """
    import torch as _torch
    import numpy as _np
    _model_path = "models/saved/ensemble_best.pt"
    _seq_len = 60
    _buy_thr  = 0.333333
    _sell_thr = 0.333333

    sig = pd.Series(0, index=df.index)
    if len(df) < _seq_len + 10:
        return sig.astype(int)
    try:
        import pathlib as _pl
        if not _pl.Path(_model_path).exists():
            return sig.astype(int)

        # 피처 생성 (120개)
        c = df["close"].values.astype(float)
        h = df["high"].values.astype(float)
        l = df["low"].values.astype(float)
        v = df["volume"].values.astype(float)
        o = df["open"].values.astype(float)
        feats = []
        feats.append(c / (_np.mean(c) + 1e-8))
        feats.append(h / (c + 1e-8))
        feats.append(l / (c + 1e-8))
        feats.append(o / (c + 1e-8))
        feats.append(v / (_np.mean(v) + 1e-8))
        for p in [1, 2, 3, 5, 10]:
            ret = _np.zeros(len(c))
            ret[p:] = (c[p:] - c[:-p]) / (c[:-p] + 1e-8)
            feats.append(ret)
        for span in [5,7,10,14,20,30,50,60,100,200]:
            ema = pd.Series(c).ewm(span=span, adjust=False).mean().values
            feats.append(ema / (c + 1e-8))
        for s1,s2 in [(5,10),(5,20),(10,20),(10,50),(20,50)]:
            e1 = pd.Series(c).ewm(span=s1).mean().values
            e2 = pd.Series(c).ewm(span=s2).mean().values
            feats.append((e1-e2)/(c+1e-8))
        for period in [7,9,14,21,28]:
            delta = _np.diff(c, prepend=c[0])
            ag = pd.Series(_np.where(delta>0,delta,0.0)).ewm(span=period).mean().values
            al = pd.Series(_np.where(delta<0,-delta,0.0)).ewm(span=period).mean().values
            feats.append((100-100/(1+ag/(al+1e-8)))/100.0)
        for (f,s,sg) in [(12,26,9),(5,13,5),(8,21,8)]:
            ef=pd.Series(c).ewm(span=f).mean().values
            es=pd.Series(c).ewm(span=s).mean().values
            mac=ef-es
            sgv=pd.Series(mac).ewm(span=sg).mean().values
            feats.append(mac/(c+1e-8)); feats.append((mac-sgv)/(c+1e-8))
        for period in [10,20,30]:
            ma=pd.Series(c).rolling(period).mean().values
            std=pd.Series(c).rolling(period).std().values
            feats.append(_np.nan_to_num((c-ma)/(std+1e-8)))
            feats.append(_np.nan_to_num((ma+2*std)/(c+1e-8)))
            feats.append(_np.nan_to_num((ma-2*std)/(c+1e-8)))
        for period in [7,14,21]:
            tr=_np.maximum(h-l,_np.maximum(abs(h-_np.roll(c,1)),abs(l-_np.roll(c,1))))
            feats.append(pd.Series(tr).ewm(span=period).mean().values/(c+1e-8))
            feats.append(_np.nan_to_num(pd.Series(c).rolling(period).std().values/(c+1e-8)))
        for period in [5,10,20,30]:
            mv=pd.Series(v).rolling(period).mean().values
            feats.append(v/(mv+1e-8))
        obv=_np.cumsum(_np.where(c>_np.roll(c,1),v,-v))
        feats.append(obv/(_np.abs(obv).max()+1e-8))
        feats.append(_np.diff(v,prepend=v[0])/(v+1e-8))
        to=c*v; feats.append(to/(to.mean()+1e-8))
        pv=c*v; feats.append(pd.Series(pv).rolling(5).mean().values/(pv.mean()+1e-8))
        body=abs(c-o)/(h-l+1e-8); upper=(h-_np.maximum(c,o))/(h-l+1e-8)
        lower=(_np.minimum(c,o)-l)/(h-l+1e-8)
        feats += [body, upper, lower, _np.where(c>o,1.0,-1.0),
                  (c-l)/(h-l+1e-8), (h-l)/(c+1e-8)]
        for period in [3,5,7,10,14,20,30]:
            feats.append(c/(pd.Series(c).shift(period).values+1e-8)-1)
        for period in [5,14,21]:
            lo_n=pd.Series(l).rolling(period).min().values
            hi_n=pd.Series(h).rolling(period).max().values
            feats.append(_np.nan_to_num((c-lo_n)/(hi_n-lo_n+1e-8)))
        lo14=pd.Series(l).rolling(14).min().values; hi14=pd.Series(h).rolling(14).max().values
        feats.append(_np.nan_to_num((hi14-c)/(hi14-lo14+1e-8)))
        tp=(h+l+c)/3; ma14t=pd.Series(tp).rolling(14).mean().values
        md14t=pd.Series(tp).rolling(14).apply(lambda x:_np.mean(_np.abs(x-x.mean()))).values
        feats.append(_np.nan_to_num((tp-ma14t)/(0.015*md14t+1e-8)/200.0))

        arr = _np.stack(feats, axis=1)
        arr = _np.nan_to_num(arr, nan=0.0, posinf=2.0, neginf=-2.0)
        if arr.shape[1] < 120:
            arr = _np.concatenate([arr, _np.zeros((arr.shape[0], 120-arr.shape[1]))], axis=1)
        else:
            arr = arr[:, :120]

        # 모델 로드 및 예측
        from models.architectures.ensemble import EnsembleModel as _EM
        _ckpt = _torch.load(_model_path, map_location="cpu", weights_only=False)
        _mdl  = _EM()
        _mdl.load_state_dict(_ckpt["model_state_dict"])
        _mdl.eval()

        with _torch.no_grad():
            for i in range(_seq_len, len(arr)):
                window = arr[i-_seq_len:i]
                x = _torch.FloatTensor(window).unsqueeze(0)
                out = _mdl(x)
                t   = out[0] if isinstance(out, tuple) else out
                val = float(t[0].mean())
                if   val >= _buy_thr:  sig.iloc[i] =  1
                elif val <= _sell_thr: sig.iloc[i] = -1

    except Exception as _e:
        import logging as _lg
        _lg.getLogger("signal_gen").warning(f"[ml_strategy] 오류: {_e}")
    return sig.astype(int)

def get_signals(strategy_name: str, df: pd.DataFrame, **kwargs) -> pd.Series:
    """get_signals 실행"""
    if strategy_name not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy_name}. : {list(STRATEGIES)}")
    fn = STRATEGIES[strategy_name]
    try:
        return fn(df, **kwargs)
    except Exception as e:
        logger.error(f"[{strategy_name}]   : {e}")
        return pd.Series(0, index=df.index)
