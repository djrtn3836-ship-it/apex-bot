"""Apex Bot -    (M1-A)
pandas    + GPU"""
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from loguru import logger


@dataclass
class BacktestConfig:
    initial_capital: float = 1_000_000
    fee_rate: float = 0.0005
    slippage: float = 0.001
    max_positions: int = 5
    stop_loss_pct: float = 0.03
    take_profit_pct: float = 0.05
    position_size_pct: float = 0.1


@dataclass
class BacktestTrade:
    market: str
    entry_time: datetime
    exit_time: Optional[datetime]
    entry_price: float
    exit_price: float
    volume: float
    amount_krw: float
    fee: float
    profit_rate: float
    profit_krw: float
    strategy: str
    exit_reason: str


@dataclass
class BacktestResult:
    config: BacktestConfig
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=pd.Series)
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    avg_profit: float = 0.0
    avg_loss: float = 0.0
    expectancy: float = 0.0


class BacktestEngine:
    """docstring"""

    VERSION = "3.0.0"

    def __init__(self, config: BacktestConfig = None):
        self.config = config or BacktestConfig()
        self.results: List[BacktestResult] = []
        logger.info(f" BacktestEngine v{self.VERSION} ")

    def run(
        self,
        df: pd.DataFrame,
        signals: pd.Series,
        market: str = "KRW-BTC",
        strategy: str = "default",
    ) -> BacktestResult:
        """df      : OHLCV DataFrame (open/high/low/close/volume)
        signals : +1=BUY, -1=SELL, 0=HOLD  (df  )"""
        result = BacktestResult(config=self.config)
        capital = self.config.initial_capital
        position = None
        equity = []

        for i in range(len(df)):
            row   = df.iloc[i]
            sig   = signals.iloc[i] if i < len(signals) else 0
            price = float(row["close"])

            # 포지션 청산 체크
            if position is not None:
                pnl = (price - position["entry"]) / position["entry"]
                should_exit = False
                exit_reason = ""

                if pnl <= -self.config.stop_loss_pct:
                    should_exit = True
                    exit_reason = "STOP_LOSS"
                elif pnl >= self.config.take_profit_pct:
                    should_exit = True
                    exit_reason = "TAKE_PROFIT"
                elif sig == -1:
                    should_exit = True
                    exit_reason = "SIGNAL_SELL"

                if should_exit:
                    exit_price  = price * (1 - self.config.slippage)
                    proceeds    = position["volume"] * exit_price
                    fee         = proceeds * self.config.fee_rate
                    profit_krw  = proceeds - fee - position["cost"]
                    profit_rate = profit_krw / position["cost"]
                    capital    += proceeds - fee

                    trade = BacktestTrade(
                        market      = market,
                        entry_time  = position["time"],
                        exit_time   = row.name if hasattr(row, "name") else datetime.now(),
                        entry_price = position["entry"],
                        exit_price  = exit_price,
                        volume      = position["volume"],
                        amount_krw  = position["cost"],
                        fee         = fee,
                        profit_rate = profit_rate,
                        profit_krw  = profit_krw,
                        strategy    = strategy,
                        exit_reason = exit_reason,
                    )
                    result.trades.append(trade)
                    position = None

            # 신규 매수
            if sig == 1 and position is None:
                buy_price  = price * (1 + self.config.slippage)
                amount     = capital * self.config.position_size_pct
                fee        = amount * self.config.fee_rate
                cost       = amount + fee
                if cost <= capital:
                    volume   = amount / buy_price
                    capital -= cost
                    position = {
                        "entry":  buy_price,
                        "volume": volume,
                        "cost":   cost,
                        "time":   row.name if hasattr(row, "name") else datetime.now(),
                    }

            # 자산 기록
            pos_value = (position["volume"] * price) if position else 0
            equity.append(capital + pos_value)

        # 결과 계산
        result.equity_curve = pd.Series(equity, index=df.index)
        result = self._calc_metrics(result)
        self.results.append(result)
        return result

    def _calc_metrics(self, result: BacktestResult) -> BacktestResult:
        """docstring"""
        trades = result.trades
        eq     = result.equity_curve
        cfg    = self.config

        result.total_trades = len(trades)

        if not trades:
            return result

        profits = [t.profit_rate for t in trades if t.profit_rate > 0]
        losses  = [t.profit_rate for t in trades if t.profit_rate <= 0]

        result.win_rate     = len(profits) / len(trades) if trades else 0
        result.avg_profit   = float(np.mean(profits)) if profits else 0
        result.avg_loss     = float(np.mean(losses))  if losses  else 0
        result.total_return = (eq.iloc[-1] - cfg.initial_capital) / cfg.initial_capital
        result.profit_factor = (
            abs(sum(profits) / sum(losses)) if losses and sum(losses) != 0 else float("inf")
        )
        result.expectancy = (
            result.win_rate * result.avg_profit
            + (1 - result.win_rate) * result.avg_loss
        )

        # 샤프비율
        returns = eq.pct_change().dropna()
        if returns.std() > 0:
            result.sharpe_ratio = float(returns.mean() / returns.std() * (365 ** 0.5))

        # MDD
        rolling_max = eq.cummax()
        drawdown    = (eq - rolling_max) / rolling_max
        result.max_drawdown = float(drawdown.min())

        return result

    def summary(self, result: BacktestResult) -> str:
        r = result
        return (
            f"\n{'='*50}\n"
            f"  백테스트 결과 요약\n"
            f"{'='*50}\n"
            f"  총 거래수  : {r.total_trades}회\n"
            f"  승률       : {r.win_rate:.1%}\n"
            f"  총 수익률  : {r.total_return:.2%}\n"
            f"  샤프비율   : {r.sharpe_ratio:.3f}\n"
            f"  최대낙폭   : {r.max_drawdown:.2%}\n"
            f"  수익비율   : {r.profit_factor:.2f}\n"
            f"  기대값     : {r.expectancy:.4f}\n"
            f"{'='*50}"
        )
