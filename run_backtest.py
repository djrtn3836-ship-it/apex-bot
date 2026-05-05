"""
APEX BOT 직접 백테스트 실행기
main.py 우회 - backtester.py + signal_generator.py 직접 호출
"""
import asyncio
import sys
sys.path.insert(0, '.')

from backtesting.backtester import Backtester
from backtesting.signal_generator import STRATEGIES
from backtesting.data_loader import fetch_ohlcv

MARKETS = ['KRW-BTC', 'KRW-ETH', 'KRW-XRP']
DAYS    = 90

async def main():
    backtester = Backtester(
        initial_capital = 114000,
        fee_rate        = 0.0005,
        slippage        = 0.001,
        stop_loss_pct   = 0.022,
        take_profit_pct = 0.045,
        position_size   = 0.20,
        max_positions   = 5,
    )

    print('=' * 65)
    print(f'  APEX BOT 백테스트 | {DAYS}일 | {len(MARKETS)}개 코인')
    print('=' * 65)

    all_results = {}

    for market in MARKETS:
        print(f'\n--- {market} ---')
        df = await fetch_ohlcv(market, '1d', DAYS)
        if df is None or len(df) < 30:
            print(f'  데이터 부족: {market}')
            continue

        for strat_name in STRATEGIES:
            result = backtester.run(df, strat_name, market)
            key = f'{market}_{strat_name}'
            all_results[key] = result

            status = '✅' if result.expectancy > 0 else '❌'
            print(
                f'  {status} {strat_name:<22} '
                f'승률={result.win_rate:.1f}% '
                f'기댓값={result.expectancy:+.4f} '
                f'샤프={result.sharpe_ratio:+.3f} '
                f'MDD={result.max_drawdown:.1f}% '
                f'거래={result.total_trades}회'
            )

    print('\n' + '=' * 65)
    print('  전략별 평균 성과 (코인 통합)')
    print('=' * 65)

    for strat_name in STRATEGIES:
        keys = [k for k in all_results if strat_name in k]
        if not keys:
            continue
        avg_wr  = sum(all_results[k].win_rate      for k in keys) / len(keys)
        avg_exp = sum(all_results[k].expectancy     for k in keys) / len(keys)
        avg_sh  = sum(all_results[k].sharpe_ratio   for k in keys) / len(keys)
        avg_mdd = sum(all_results[k].max_drawdown   for k in keys) / len(keys)
        verdict = '🟢 사용가능' if avg_exp > 0 and avg_wr >= 50 else '🔴 제외권고'
        print(
            f'  {verdict} {strat_name:<22} '
            f'평균승률={avg_wr:.1f}% '
            f'기댓값={avg_exp:+.4f} '
            f'샤프={avg_sh:+.3f} '
            f'MDD={avg_mdd:.1f}%'
        )

    print('\n백테스트 완료')

asyncio.run(main())
