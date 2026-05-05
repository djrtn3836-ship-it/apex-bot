"""
OrderBlock SMC 개선 v2 - EMA 기간 수정 버전
- 데이터: 365일 (EMA200 충분히 확보)
- EMA200 → EMA50 으로 교체 (50일 데이터면 충분)
- 추가: EMA20/50 크로스 버전
"""
import asyncio, sys
sys.path.insert(0, '.')
import numpy as np
import pandas as pd
from backtesting.backtester import Backtester
from backtesting.data_loader import fetch_ohlcv
from backtesting.signal_generator import STRATEGIES

def _ema(s, n): return s.ewm(span=n, adjust=False).mean()
def _rsi(c, n=14):
    d = c.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - (100 / (1 + g / l.replace(0, np.nan)))

# ── 원본 ──────────────────────────────────────────────────────
def signal_ob_original(df, swing=5):
    lh = df["high"].rolling(swing*2+1, center=False).max().shift(1)
    ll = df["low"].rolling(swing*2+1,  center=False).min().shift(1)
    sig = pd.Series(0, index=df.index)
    sig[df["close"] <= ll * 1.02] =  1
    sig[df["close"] >= lh * 0.98] = -1
    return sig.astype(int)

# ── V1: EMA50 추세 필터 (EMA200 대신) ────────────────────────
def signal_ob_v1_ema50(df, swing=5):
    lh    = df["high"].rolling(swing*2+1, center=False).max().shift(1)
    ll    = df["low"].rolling(swing*2+1,  center=False).min().shift(1)
    ema50 = _ema(df["close"], 50)
    sig   = pd.Series(0, index=df.index)
    sig[(df["close"] <= ll * 1.02) & (df["close"] > ema50)]  =  1
    sig[(df["close"] >= lh * 0.98) & (df["close"] < ema50)]  = -1
    return sig.astype(int)

# ── V2: EMA20/50 크로스 추세 ──────────────────────────────────
def signal_ob_v2_cross(df, swing=5):
    lh    = df["high"].rolling(swing*2+1, center=False).max().shift(1)
    ll    = df["low"].rolling(swing*2+1,  center=False).min().shift(1)
    ema20 = _ema(df["close"], 20)
    ema50 = _ema(df["close"], 50)
    bull  = ema20 > ema50
    bear  = ema20 < ema50
    sig   = pd.Series(0, index=df.index)
    sig[(df["close"] <= ll * 1.02) & bull] =  1
    sig[(df["close"] >= lh * 0.98) & bear] = -1
    return sig.astype(int)

# ── V3: EMA20/50 + RSI ────────────────────────────────────────
def signal_ob_v3_rsi(df, swing=5):
    lh    = df["high"].rolling(swing*2+1, center=False).max().shift(1)
    ll    = df["low"].rolling(swing*2+1,  center=False).min().shift(1)
    ema20 = _ema(df["close"], 20)
    ema50 = _ema(df["close"], 50)
    rsi   = _rsi(df["close"])
    bull  = ema20 > ema50
    bear  = ema20 < ema50
    sig   = pd.Series(0, index=df.index)
    sig[(df["close"] <= ll * 1.02) & bull & (rsi < 50)] =  1
    sig[(df["close"] >= lh * 0.98) & bear & (rsi > 50)] = -1
    return sig.astype(int)

# ── V4: EMA50 + RSI + 거래량 ─────────────────────────────────
def signal_ob_v4_full(df, swing=5):
    lh      = df["high"].rolling(swing*2+1, center=False).max().shift(1)
    ll      = df["low"].rolling(swing*2+1,  center=False).min().shift(1)
    ema50   = _ema(df["close"], 50)
    rsi     = _rsi(df["close"])
    avg_vol = df["volume"].rolling(20).mean()
    vol_ok  = df["volume"] >= avg_vol * 1.0
    sig     = pd.Series(0, index=df.index)
    sig[(df["close"] <= ll * 1.02) & (df["close"] > ema50) & (rsi < 50) & vol_ok] =  1
    sig[(df["close"] >= lh * 0.98) & (df["close"] < ema50) & (rsi > 50) & vol_ok] = -1
    return sig.astype(int)

# ── V5: 스윙 범위 확대 (swing=10) + EMA50 ────────────────────
def signal_ob_v5_wide(df, swing=10):
    lh    = df["high"].rolling(swing*2+1, center=False).max().shift(1)
    ll    = df["low"].rolling(swing*2+1,  center=False).min().shift(1)
    ema50 = _ema(df["close"], 50)
    sig   = pd.Series(0, index=df.index)
    sig[(df["close"] <= ll * 1.03) & (df["close"] > ema50)] =  1
    sig[(df["close"] >= lh * 0.97) & (df["close"] < ema50)] = -1
    return sig.astype(int)

MARKETS = [
    'KRW-BTC','KRW-ETH','KRW-XRP','KRW-SOL','KRW-ADA',
    'KRW-DOGE','KRW-AVAX','KRW-DOT','KRW-LINK','KRW-ATOM'
]
DAYS = 365

VERSIONS = {
    "원본(기준선)":         signal_ob_original,
    "V1(EMA50추세)":        signal_ob_v1_ema50,
    "V2(EMA20/50크로스)":   signal_ob_v2_cross,
    "V3(크로스+RSI)":       signal_ob_v3_rsi,
    "V4(EMA50+RSI+거래량)": signal_ob_v4_full,
    "V5(스윙확대+EMA50)":   signal_ob_v5_wide,
}

async def main():
    bt = Backtester(
        initial_capital=114000, fee_rate=0.0005,
        slippage=0.001, stop_loss_pct=0.022,
        take_profit_pct=0.045, position_size=0.20
    )

    print(f'OrderBlock 개선 v2 | {DAYS}일 | {len(MARKETS)}개 코인')
    print('='*72)

    dfs = {}
    for mkt in MARKETS:
        df = await fetch_ohlcv(mkt, '1d', DAYS)
        if df is not None and len(df) >= 60:
            dfs[mkt] = df
    print(f'데이터 로드: {len(dfs)}개 코인 | 평균 {int(sum(len(v) for v in dfs.values())/len(dfs))}봉\n')

    summary = {}
    for ver_name, sig_fn in VERSIONS.items():
        wins, total, exp_sum, sharpe_sum, mdd_sum = 0, 0, 0.0, 0.0, 0.0
        coin_results = []

        for mkt, df in dfs.items():
            STRATEGIES['_custom_'] = sig_fn
            r = bt.run(df, '_custom_', mkt)
            del STRATEGIES['_custom_']
            coin_results.append((mkt, r))
            total     += r.total_trades
            wins      += int(r.win_rate * r.total_trades / 100)
            exp_sum   += r.expectancy
            sharpe_sum+= r.sharpe_ratio
            mdd_sum   += r.max_drawdown

        n         = len(dfs)
        wr        = wins/total*100 if total else 0
        avg_exp   = exp_sum/n
        avg_sh    = sharpe_sum/n
        avg_mdd   = mdd_sum/n

        if   wr >= 55 and avg_exp > 0 and avg_sh > 0.3:
            verdict = '🟢 실거래가능'
        elif wr >= 50 and avg_exp > 0:
            verdict = '🟡 조건부가능'
        else:
            verdict = '🔴 제외권고  '

        summary[ver_name] = dict(wr=wr,exp=avg_exp,sh=avg_sh,mdd=avg_mdd,tr=total,verdict=verdict)

        print(f'{verdict} [{ver_name:<20}] '
              f'승률={wr:.1f}% 기댓값={avg_exp:+.4f} '
              f'샤프={avg_sh:+.3f} MDD={avg_mdd:.1f}% 거래={total}회')

    print('\n' + '='*72)
    best_k = max(summary, key=lambda k: (summary[k]['exp'], summary[k]['wr']))
    b = summary[best_k]
    print(f'🏆 최고: [{best_k}] 승률={b["wr"]:.1f}% 기댓값={b["exp"]:+.4f} 샤프={b["sh"]:+.3f} 거래={b["tr"]}회')

    if b['exp'] > 0 and b['wr'] >= 50 and b['tr'] >= 30:
        print('\n✅ 조건 통과 → signal_generator.py 업데이트 진행')
    else:
        print('\n⚠️  추가 전략 탐색 필요 (아래 2단계로 진행)')
        print('   → trend_following 단독 365일 검증 추천')

asyncio.run(main())
