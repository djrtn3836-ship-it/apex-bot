import asyncio, sys
sys.path.insert(0, '.')
from backtesting.backtester import Backtester
from backtesting.data_loader import fetch_ohlcv

MARKETS = ['KRW-BTC','KRW-ETH','KRW-XRP','KRW-SOL','KRW-ADA',
           'KRW-DOGE','KRW-AVAX','KRW-DOT','KRW-LINK','KRW-ATOM']
DAYS = 180

async def main():
    bt = Backtester(
        initial_capital=114000, fee_rate=0.0005,
        slippage=0.001, stop_loss_pct=0.022,
        take_profit_pct=0.045, position_size=0.20
    )
    print(f'order_block_smc 집중 검증 | {DAYS}일 | {len(MARKETS)}개 코인')
    print('='*60)
    wins, total, exp_sum = 0, 0, 0
    for mkt in MARKETS:
        df = await fetch_ohlcv(mkt, '1d', DAYS)
        if df is None or len(df) < 30:
            continue
        r = bt.run(df, 'order_block_smc', mkt)
        status = '✅' if r.expectancy > 0 else '❌'
        print(f'{status} {mkt:<12} 승률={r.win_rate:.1f}% 기댓값={r.expectancy:+.4f} 거래={r.total_trades}회 샤프={r.sharpe_ratio:+.3f}')
        total += r.total_trades
        wins  += int(r.win_rate * r.total_trades / 100)
        exp_sum += r.expectancy
    print('='*60)
    overall_wr = wins/total*100 if total else 0
    print(f'종합: 총거래={total}회 | 전체승률={overall_wr:.1f}% | 평균기댓값={exp_sum/len(MARKETS):+.4f}')
    if total >= 30 and overall_wr >= 55:
        print('🟢 실거래 투입 가능 수준')
    elif total >= 20 and overall_wr >= 50:
        print('🟡 조건부 투입 가능 (추가 검증 권장)')
    else:
        print('🔴 아직 실거래 투입 불가')

asyncio.run(main())
