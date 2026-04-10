"""Apex Bot -   (M1-C)
1000"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List
from loguru import logger
from .backtest_engine import BacktestResult


@dataclass
class MCResult:
    n_simulations:   int   = 1000
    percentile_5:    float = 0.0
    percentile_25:   float = 0.0
    median_return:   float = 0.0
    percentile_75:   float = 0.0
    percentile_95:   float = 0.0
    ruin_probability: float = 0.0
    expected_return: float = 0.0
    is_viable:       bool  = False
    all_returns:     List[float] = field(default_factory=list)


class MonteCarloSimulator:
    """docstring"""

    def __init__(self, n_simulations: int = 1000):
        self.n = n_simulations
        logger.info(f" MonteCarloSimulator  ({n_simulations})")

    def run(
        self,
        backtest_result: BacktestResult,
        ruin_threshold: float = -0.20,
    ) -> MCResult:
        """ruin_threshold:    ( -20%)"""
        trades = backtest_result.trades
        if not trades:
            logger.warning("   —  ")
            return MCResult()

        trade_returns = np.array([t.profit_rate for t in trades])
        all_final     = []

        for _ in range(self.n):
            shuffled   = np.random.choice(trade_returns, size=len(trade_returns), replace=True)
            cumulative = np.cumprod(1 + shuffled) - 1
            all_final.append(float(cumulative[-1]))

        all_final = np.array(all_final)
        ruin_cnt  = np.sum(all_final <= ruin_threshold)

        result = MCResult(
            n_simulations    = self.n,
            percentile_5     = float(np.percentile(all_final, 5)),
            percentile_25    = float(np.percentile(all_final, 25)),
            median_return    = float(np.median(all_final)),
            percentile_75    = float(np.percentile(all_final, 75)),
            percentile_95    = float(np.percentile(all_final, 95)),
            ruin_probability = float(ruin_cnt / self.n),
            expected_return  = float(np.mean(all_final)),
            all_returns      = all_final.tolist(),
        )
        result.is_viable = (
            result.ruin_probability < 0.10
            and result.median_return > 0
            and result.percentile_5  > -0.30
        )

        logger.info(
            f" : "
            f"={result.median_return:.2%} | "
            f"={result.ruin_probability:.1%} | "
            f"={'' if result.is_viable else ''}"
        )
        return result

    def summary(self, r: MCResult) -> str:
        return (
            f"\n{'='*50}\n"
            f"  몬테카를로 결과 ({r.n_simulations}회)\n"
            f"{'='*50}\n"
            f"  5%ile  수익률: {r.percentile_5:.2%}\n"
            f"  중앙값 수익률: {r.median_return:.2%}\n"
            f"  95%ile 수익률: {r.percentile_95:.2%}\n"
            f"  파산 확률    : {r.ruin_probability:.1%}\n"
            f"  전략 생존가능: {'✅' if r.is_viable else '❌'}\n"
            f"{'='*50}"
        )
