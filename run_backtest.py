"""APEX BOT   
:
  python run_backtest.py                          # KRW-BTC   1
  python run_backtest.py --market KRW-ETH         #  
  python run_backtest.py --days 180               #  
  python run_backtest.py --strategy ml_strategy   #  
  python run_backtest.py --walk-forward           # Walk-Forward 
  python run_backtest.py --all-coins              # 10  
  python run_backtest.py --ensemble               #  agree  
  python run_backtest.py --ensemble --all-coins   # 10"""
import asyncio
import argparse
from loguru import logger

from backtesting.backtester import Backtester
from backtesting.data_loader import fetch_ohlcv
from backtesting.signal_generator import STRATEGIES
from backtesting.report.performance_report import PerformanceReporter

COINS = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP",
    "KRW-SOL", "KRW-ADA", "KRW-DOGE",
    "KRW-DOT", "KRW-LINK", "KRW-AVAX", "KRW-ATOM",
]


async def run(args):
    bt = Backtester(
        initial_capital = 1_000_000,
        fee_rate        = 0.0005,
        slippage        = 0.001,
        stop_loss_pct   = 0.05,
        take_profit_pct = 0.10,
        position_size   = 0.20,
    )
    reporter = PerformanceReporter()
    coins    = COINS if args.all_coins else [args.market]


    # 국면 적응 전략 모드
    if args.regime:
        from backtesting.regime_strategy import RegimeStrategyBacktester
        rbt = RegimeStrategyBacktester(bt)
        for coin in coins:
            df = await fetch_ohlcv(coin, args.interval, args.days)
            if df.empty:
                continue
            rbt.compare_min_score(df, coin)
        return

    # 앙상블 모드
    if args.ensemble:
        from backtesting.ensemble_backtest import EnsembleBacktester
        ens = EnsembleBacktester(bt)
        for coin in coins:
            df = await fetch_ohlcv(coin, args.interval, args.days)
            if df.empty:
                logger.warning(f"{coin}  , ")
                continue
            print(f"\n[{coin}]  agree  ")
            ens.compare_agree_levels(df, ens.DEFAULT_STRATEGIES, coin)
        return

    # 단일 전략 모드
    if args.strategy:
        coin = args.market
        logger.info(f"  : {args.strategy} / {coin} / {args.days}")
        df = await fetch_ohlcv(coin, args.interval, args.days)
        if df.empty:
            logger.error(" ")
            return
        if args.walk_forward:
            results = bt.walk_forward(df, args.strategy, coin, n_splits=5)
            for r in results:
                r.print_summary()
            logger.info(f"Walk-Forward : {len(results)}구간")
        else:
            result = bt.run(df, args.strategy, coin)
            reporter.generate(result, args.strategy)
        return

    # 전체 전략 모드
    for coin in coins:
        logger.info("=" * 50)
        logger.info(f"[{coin}]    ({args.days})")
        logger.info("=" * 50)

        df = await fetch_ohlcv(coin, args.interval, args.days)
        if df.empty:
            logger.warning(f"{coin}  , ")
            continue

        results = []
        names   = []
        for name in STRATEGIES:
            r = bt.run(df, name, coin)
            results.append(r)
            names.append(name)

        reporter.generate_comparison(results, names)

        if args.walk_forward:
            best = max(results, key=lambda x: x.sharpe_ratio)
            logger.info(f"[Walk-Forward]   : {best.strategy}")
            wf_results = bt.walk_forward(df, best.strategy, coin)
            total_ret  = sum(r.total_return for r in wf_results)
            logger.info(f"Walk-Forward  : {total_ret:.2f}%")

    logger.info("  !  reports/backtest/  .")


def main():
    parser = argparse.ArgumentParser(description="APEX BOT 백테스터")
    parser.add_argument("--market",       default="KRW-BTC",    help="코인 마켓 (기본: KRW-BTC)")
    parser.add_argument("--interval",     default="1d",          help="캔들 주기 (기본: 1d)")
    parser.add_argument("--days",         default=365, type=int, help="백테스트 기간 (기본: 365)")
    parser.add_argument("--strategy",     default=None,          help="단일 전략 이름")
    parser.add_argument("--walk-forward", action="store_true",   help="Walk-Forward 분석")
    parser.add_argument("--regime",       action="store_true",   help="국면 감지 적응형 전략")
    parser.add_argument("--ensemble",     action="store_true",   help="앙상블 agree 레벨 비교")
    parser.add_argument("--all-coins",    action="store_true",   help="10개 코인 전체 테스트")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
