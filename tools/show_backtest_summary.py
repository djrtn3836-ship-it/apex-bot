"""
백테스트 기반 전략 매핑 요약 출력
실행: python tools/show_backtest_summary.py
"""
from config.strategy_mapping import COIN_STRATEGY_MAP, STRATEGY_STATS, STRATEGY_PRIORITY

print()
print("=" * 60)
print("  📊 코인별 최적 전략 매핑 (백테스트 기반)")
print("=" * 60)
print(f"  {'코인':<12} {'전략':<22} {'샤프':>6} {'수익률':>7} {'승률':>7}")
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
print("  📈 전략 우선순위 (평균 샤프 기준):")
for i, s in enumerate(STRATEGY_PRIORITY, 1):
    print(f"    {i}. {s}")
print()
