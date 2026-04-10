""": python tools/show_backtest_summary.py"""
from config.strategy_mapping import COIN_STRATEGY_MAP, STRATEGY_STATS, STRATEGY_PRIORITY

print()
print("=" * 60)
print("       ( )")
print("=" * 60)
print(f"  {'':<12} {'':<22} {'':>6} {'':>7} {'':>7}")
print("-" * 60)

for coin, stats in STRATEGY_STATS.items():
    print(
        f"  {coin:<12} "
        f"{stats['strategy']:<22} "
        f"{stats['sharpe']:>6.3f} "
        f"{stats['return']:>6.1f}% "
        f"{stats['win_rate']:>6.1f}%"
    )

print("=" * 60)
print()
print("     (  ):")
for i, s in enumerate(STRATEGY_PRIORITY, 1):
    print(f"    {i}. {s}")
print()
