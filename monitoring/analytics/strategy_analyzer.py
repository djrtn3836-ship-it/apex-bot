"""Apex Bot -    (M7-A)"""
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from loguru import logger


@dataclass
class StrategyStats:
    strategy:        str
    total_trades:    int   = 0
    win_rate:        float = 0.0
    avg_profit:      float = 0.0
    avg_loss:        float = 0.0
    profit_factor:   float = 0.0
    sharpe_ratio:    float = 0.0
    max_drawdown:    float = 0.0
    expectancy:      float = 0.0
    max_consec_loss: int   = 0
    total_pnl:       float = 0.0
    grade:           str   = "F"


class StrategyAnalyzer:
    """StrategyAnalyzer 클래스"""

    GRADE_THRESHOLDS = {
        "S": {"win_rate": 0.60, "sharpe": 1.5, "expectancy": 0.005},
        "A": {"win_rate": 0.55, "sharpe": 1.0, "expectancy": 0.003},
        "B": {"win_rate": 0.50, "sharpe": 0.5, "expectancy": 0.001},
        "C": {"win_rate": 0.45, "sharpe": 0.0, "expectancy": 0.000},
    }

    def __init__(self):
        logger.info(" StrategyAnalyzer ")

    def analyze(self, trades_df: pd.DataFrame) -> Dict[str, StrategyStats]:
        """trades_df: trade_history DataFrame
        : strategy, profit_rate, side"""
        results = {}
        if trades_df.empty:
            return results

        sells = trades_df[trades_df["side"] == "SELL"].copy()
        if sells.empty:
            return results

        for strategy, group in sells.groupby("strategy"):
            stats = self._calc_stats(strategy, group)
            results[strategy] = stats

        return results

    def _calc_stats(self, strategy: str, df: pd.DataFrame) -> StrategyStats:
        rates   = df["profit_rate"].fillna(0).tolist()
        profits = [r for r in rates if r > 0]
        losses  = [r for r in rates if r <= 0]

        stats = StrategyStats(strategy=strategy)
        stats.total_trades  = len(rates)
        stats.win_rate      = len(profits) / len(rates) if rates else 0
        stats.avg_profit    = float(np.mean(profits)) if profits else 0
        stats.avg_loss      = float(np.mean(losses))  if losses  else 0
        stats.total_pnl     = float(sum(rates))
        stats.profit_factor = (
            abs(sum(profits) / sum(losses))
            if losses and sum(losses) != 0 else float("inf")
        )
        stats.expectancy    = (
            stats.win_rate * stats.avg_profit
            + (1 - stats.win_rate) * stats.avg_loss
        )

        # 샤프
        arr = np.array(rates)
        if arr.std() > 0:
            stats.sharpe_ratio = float(arr.mean() / arr.std() * (252 ** 0.5))

        # MDD
        cum = np.cumprod(1 + arr) if len(arr) > 0 else np.array([1.0])
        peak = np.maximum.accumulate(cum)
        dd   = (cum - peak) / peak
        stats.max_drawdown = float(dd.min()) if len(dd) > 0 else 0

        # 연속 손실
        max_consec = cur_consec = 0
        for r in rates:
            if r <= 0:
                cur_consec += 1
                max_consec  = max(max_consec, cur_consec)
            else:
                cur_consec  = 0
        stats.max_consec_loss = max_consec

        # 등급
        stats.grade = self._grade(stats)
        return stats

    def _grade(self, s: StrategyStats) -> str:
        for grade, thr in self.GRADE_THRESHOLDS.items():
            if (s.win_rate    >= thr["win_rate"]
                and s.sharpe_ratio >= thr["sharpe"]
                and s.expectancy   >= thr["expectancy"]):
                return grade
        return "F"

    def print_report(self, results: Dict[str, StrategyStats]):
        print(f"\n{'='*60}")
        print("     ")
        print(f"{'='*60}")
        for name, s in sorted(results.items(), key=lambda x: -x[1].expectancy):
            print(
                f"  [{s.grade}] {name:<25} "
                f":{s.total_trades:3d} | "
                f":{s.win_rate:.0%} | "
                f":{s.sharpe_ratio:.2f} | "
                f":{s.expectancy:+.4f}"
            )
        print("="*60)
