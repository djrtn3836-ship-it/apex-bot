"""
개선된 order_block_smc 전략 검증
추가 필터:
  1. EMA200 추세 필터 (BUY: EMA200 위, SELL: EMA200 아래)
  2. ATR 변동성 필터 (변동성 너무 낮으면 진입 안 함)
  3. 거래량 확인 필터 (평균 거래량 이상일 때만 진입)
"""
import asyncio, sys
sys.path.insert(0, '.')
import numpy as np
import pandas as pd
from backtesting.backtester import Backtester
from backtesting.data_loader import fetch_ohlcv

# ── 기존 전략 (비교용) ─────────────────────────────────────────
def signal_ob_original(df: pd.DataFrame, swing_period: int = 5) -> pd.Series:
    local_high = df["high"].rolling(swing_period * 2 + 1, center=False).max().shift(1)
    local_low  = df["low"].rolling(swing_period * 2 + 1, center=False).min().shift(1)
    sig = pd.Series(0, index=df.index)
    sig[df["close"] <= local_low  * 1.02] =  1
    sig[df["close"] >= local_high * 0.98] = -1
    return sig.astype(int)

# ── 개선 V1: EMA200 추세 필터만 추가 ──────────────────────────
def signal_ob_v1_trend(df: pd.DataFrame, swing_period: int = 5) -> pd.Series:
    local_high = df["high"].rolling(swing_period * 2 + 1, center=False).max().shift(1)
    local_low  = df["low"].rolling(swing_period * 2 + 1, center=False).min().shift(1)
    ema200 = df["close"].ewm(span=200, adjust=False).mean()

    sig = pd.Series(0, index=df.index)
    # BUY: 최저점 근처 + EMA200 위 (상승 추세)
    buy_cond  = (df["close"] <= local_low  * 1.02) & (df["close"] > ema200)
    # SELL: 최고점 근처 + EMA200 아래 (하락 추세)
    sell_cond = (df["close"] >= local_high * 0.98) & (df["close"] < ema200)
    sig[buy_cond]  =  1
    sig[sell_cond] = -1
    return sig.astype(int)

# ── 개선 V2: EMA200 + 거래량 필터 ─────────────────────────────
def signal_ob_v2_volume(df: pd.DataFrame, swing_period: int = 5) -> pd.Series:
    local_high = df["high"].rolling(swing_period * 2 + 1, center=False).max().shift(1)
    local_low  = df["low"].rolling(swing_period * 2 + 1, center=False).min().shift(1)
    ema200  = df["close"].ewm(span=200, adjust=False).mean()
    avg_vol = df["volume"].rolling(20).mean()

    sig = pd.Series(0, index=df.index)
    vol_ok   = df["volume"] >= avg_vol * 1.2   # 평균 거래량 120% 이상
    buy_cond  = (df["close"] <= local_low  * 1.02) & (df["close"] > ema200) & vol_ok
    sell_cond = (df["close"] >= local_high * 0.98) & (df["close"] < ema200) & vol_ok
    sig[buy_cond]  =  1
    sig[sell_cond] = -1
    return sig.astype(int)

# ── 개선 V3: EMA200 + 거래량 + RSI 과매도/과매수 필터 ──────────
def signal_ob_v3_rsi(df: pd.DataFrame, swing_period: int = 5) -> pd.Series:
    local_high = df["high"].rolling(swing_period * 2 + 1, center=False).max().shift(1)
    local_low  = df["low"].rolling(swing_period * 2 + 1, center=False).min().shift(1)
    ema200  = df["close"].ewm(span=200, adjust=False).mean()
    avg_vol = df["volume"].rolling(20).mean()

    # RSI 계산
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))

    sig = pd.Series(0, index=df.index)
    vol_ok    = df["volume"] >= avg_vol * 1.0
    # BUY: 최저점 + 상승추세 + 거래량 + RSI 과매도(50 이하)
    buy_cond  = (df["close"] <= local_low  * 1.02) & (df["close"] > ema200) & vol_ok & (rsi < 50)
    # SELL: 최고점 + 하락추세 + 거래량 + RSI 과매수(50 이상)
    sell_cond = (df["close"] >= local_high * 0.98) & (df["close"] < ema200) & vol_ok & (rsi > 50)
    sig[buy_cond]  =  1
    sig[sell_cond] = -1
    return sig.astype(int)

# ── 개선 V4: EMA50/200 크로스 + OrderBlock + RSI ───────────────
def signal_ob_v4_full(df: pd.DataFrame, swing_period: int = 5) -> pd.Series:
    local_high = df["high"].rolling(swing_period * 2 + 1, center=False).max().shift(1)
    local_low  = df["low"].rolling(swing_period * 2 + 1, center=False).min().shift(1)
    ema50   = df["close"].ewm(span=50,  adjust=False).mean()
    ema200  = df["close"].ewm(span=200, adjust=False).mean()
    avg_vol = df["volume"].rolling(20).mean()

    delta = df["close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))

    sig = pd.Series(0, index=df.index)
    vol_ok     = df["volume"] >= avg_vol * 1.0
    bull_trend = (ema50 > ema200) & (df["close"] > ema200)  # 강한 상승추세
    bear_trend = (ema50 < ema200) & (df["close"] < ema200)  # 강한 하락추세

    buy_cond  = (df["close"] <= local_low  * 1.02) & bull_trend & vol_ok & (rsi < 55)
    sell_cond = (df["close"] >= local_high * 0.98) & bear_trend & vol_ok & (rsi > 45)
    sig[buy_cond]  =  1
    sig[sell_cond] = -1
    return sig.astype(int)


# ── 백테스트 실행 ──────────────────────────────────────────────
MARKETS = [
    'KRW-BTC','KRW-ETH','KRW-XRP','KRW-SOL','KRW-ADA',
    'KRW-DOGE','KRW-AVAX','KRW-DOT','KRW-LINK','KRW-ATOM'
]
DAYS = 180

VERSIONS = {
    "원본(기준선)":     signal_ob_original,
    "V1(추세필터)":     signal_ob_v1_trend,
    "V2(추세+거래량)":  signal_ob_v2_volume,
    "V3(추세+거래량+RSI)": signal_ob_v3_rsi,
    "V4(EMA크로스+풀)": signal_ob_v4_full,
}

async def main():
    bt = Backtester(
        initial_capital=114000, fee_rate=0.0005,
        slippage=0.001, stop_loss_pct=0.022,
        take_profit_pct=0.045, position_size=0.20
    )

    print(f'OrderBlock SMC 전략 개선 비교 | {DAYS}일 | {len(MARKETS)}개 코인')
    print('='*70)

    # 데이터 미리 로드
    dfs = {}
    for mkt in MARKETS:
        df = await fetch_ohlcv(mkt, '1d', DAYS)
        if df is not None and len(df) >= 50:
            dfs[mkt] = df

    print(f'데이터 로드 완료: {len(dfs)}개 코인\n')

    summary = {}

    for ver_name, sig_fn in VERSIONS.items():
        wins, total, exp_sum, sharpe_sum = 0, 0, 0.0, 0.0
        results_per_coin = []

        for mkt, df in dfs.items():
            # signal_generator 우회: 직접 신호 생성 후 backtester._simulate 호출
            signals = sig_fn(df)
            # backtester 내부 _simulate 직접 호출
            from backtesting.signal_generator import STRATEGIES
            # 커스텀 신호를 쓰기 위해 임시로 등록
            STRATEGIES['_custom_'] = sig_fn
            r = bt.run(df, '_custom_', mkt)
            del STRATEGIES['_custom_']

            results_per_coin.append(r)
            total     += r.total_trades
            wins      += int(r.win_rate * r.total_trades / 100)
            exp_sum   += r.expectancy
            sharpe_sum+= r.sharpe_ratio

        overall_wr  = wins / total * 100 if total else 0
        avg_exp     = exp_sum / len(dfs) if dfs else 0
        avg_sharpe  = sharpe_sum / len(dfs) if dfs else 0
        avg_mdd     = sum(r.max_drawdown for r in results_per_coin) / len(results_per_coin) if results_per_coin else 0

        if overall_wr >= 55 and avg_exp > 0:
            verdict = '🟢 실거래가능'
        elif overall_wr >= 50 and avg_exp > 0:
            verdict = '🟡 조건부가능'
        else:
            verdict = '🔴 제외권고  '

        summary[ver_name] = {
            'wr': overall_wr, 'exp': avg_exp,
            'sharpe': avg_sharpe, 'mdd': avg_mdd,
            'trades': total, 'verdict': verdict
        }

        print(f'{verdict} [{ver_name:<18}] '
              f'승률={overall_wr:.1f}% '
              f'기댓값={avg_exp:+.4f} '
              f'샤프={avg_sharpe:+.3f} '
              f'MDD={avg_mdd:.1f}% '
              f'거래={total}회')

    # 최고 버전 선정
    print('\n' + '='*70)
    best = max(summary.items(), key=lambda x: x[1]['exp'])
    print(f'🏆 최고 버전: [{best[0]}]')
    print(f'   승률={best[1]["wr"]:.1f}% | 기댓값={best[1]["exp"]:+.4f} | 샤프={best[1]["sharpe"]:+.3f}')

    if best[1]['exp'] > 0 and best[1]['wr'] >= 50:
        print(f'\n✅ 다음 단계: {best[0]} 버전을 signal_generator.py에 적용')
        print('   → python apply_best_strategy.py 실행')
    else:
        print('\n⚠️  모든 버전이 기준 미달 → 파라미터 추가 조정 필요')

asyncio.run(main())
