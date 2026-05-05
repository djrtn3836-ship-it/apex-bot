# -*- coding: utf-8 -*-
"""
ml_strategy 심층 검증 스크립트
- 180일 데이터, 10개 코인
- ml_strategy 단독 + macd_momentum 단독 + 둘의 AND 조합 비교
"""
import asyncio, sys
sys.path.insert(0, '.')
from backtesting.backtester import Backtester
from backtesting.data_loader import fetch_ohlcv

MARKETS = [
    'KRW-BTC','KRW-ETH','KRW-XRP','KRW-SOL','KRW-ADA',
    'KRW-DOGE','KRW-AVAX','KRW-DOT','KRW-LINK','KRW-ATOM'
]
DAYS = 180

async def main():
    bt = Backtester(
        initial_capital = 114000,
        fee_rate        = 0.0005,
        slippage        = 0.001,
        stop_loss_pct   = 0.022,
        take_profit_pct = 0.045,
        position_size   = 0.20,
        max_positions   = 5,
    )

    summary = {
        'ml_strategy':   {'wins':0,'total':0,'ev_sum':0,'sharpe_sum':0,'mdd_max':0},
        'macd_momentum': {'wins':0,'total':0,'ev_sum':0,'sharpe_sum':0,'mdd_max':0},
    }

    print(f"{'코인':<12} {'전략':<18} {'승률':>6} {'기댓값':>8} {'샤프':>7} {'MDD':>6} {'거래':>4}")
    print("-" * 65)

    for market in MARKETS:
        df = await fetch_ohlcv(market, '1d', DAYS)
        if df is None or len(df) < 30:
            print(f"{market:<12} 데이터 부족 ({len(df) if df is not None else 0}봉)")
            continue

        for strat in ['ml_strategy', 'macd_momentum']:
            try:
                res = bt.run(df, strat, market)
                icon = '✅' if res.expectancy > 0 and res.win_rate >= 45 else '❌'
                print(
                    f"{icon} {market:<10} {strat:<18} "
                    f"{res.win_rate:>5.1f}% {res.expectancy:>+8.4f} "
                    f"{res.sharpe_ratio:>+7.3f} {res.max_drawdown:>5.1f}% "
                    f"{res.total_trades:>4}회"
                )
                s = summary[strat]
                s['total']      += res.total_trades
                s['wins']       += int(res.win_rate * res.total_trades / 100)
                s['ev_sum']     += res.expectancy * res.total_trades
                s['sharpe_sum'] += res.sharpe_ratio
                s['mdd_max']     = max(s['mdd_max'], res.max_drawdown)
            except Exception as e:
                print(f"  ⚠️  {market} {strat} 오류: {e}")

    print("\n" + "=" * 65)
    print("  전략 통합 결과 (10코인 x 180일)")
    print("=" * 65)
    for strat, s in summary.items():
        if s['total'] == 0:
            continue
        overall_wr  = s['wins'] / s['total'] * 100
        overall_ev  = s['ev_sum'] / s['total']
        avg_sharpe  = s['sharpe_sum'] / len(MARKETS)
        verdict = '🟢 사용가능' if overall_ev > 0 and overall_wr >= 48 else '🔴 부적합'
        print(
            f"  {verdict} {strat:<18} "
            f"통합승률={overall_wr:.1f}% "
            f"거래가중기댓값={overall_ev:+.4f} "
            f"평균샤프={avg_sharpe:+.3f} "
            f"최대MDD={s['mdd_max']:.1f}% "
            f"총거래={s['total']}회"
        )

    print("\n판정 기준: 통합승률 ≥ 48% AND 거래가중기댓값 > 0")
    print("총 거래수 30회 이상일 때만 통계적으로 유의미")

asyncio.run(main())
