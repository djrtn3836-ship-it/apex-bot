"""APEX BOT -"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


def create_test_df(n: int = 500) -> pd.DataFrame:
    """DataFrame"""
    dates = [datetime.now() - timedelta(hours=n-i) for i in range(n)]
    close = 50_000_000 + np.cumsum(np.random.randn(n) * 200_000)
    close = np.maximum(close, 10_000_000)
    high = close + np.abs(np.random.randn(n)) * 100_000
    low = close - np.abs(np.random.randn(n)) * 100_000

    df = pd.DataFrame({
        "open": close + np.random.randn(n) * 50_000,
        "high": high,
        "low": np.minimum(low, close),
        "close": close,
        "volume": np.random.exponential(5, n),
        "atr": np.abs(np.random.randn(n)) * 300_000 + 200_000,
    }, index=pd.DatetimeIndex(dates))
    return df.sort_index()


def random_signal_fn(df: pd.DataFrame) -> pd.Series:
    """()"""
    signals = pd.Series(0, index=df.index)
    for i in range(len(df)):
        r = np.random.random()
        if r < 0.1:
            signals.iloc[i] = 1   # 매수
        elif r > 0.9:
            signals.iloc[i] = -1  # 매도
    return signals


def buy_hold_signal_fn(df: pd.DataFrame) -> pd.Series:
    """(  ,  )"""
    signals = pd.Series(0, index=df.index)
    signals.iloc[0] = 1
    return signals


class TestBacktester:
    @pytest.mark.asyncio
    async def test_basic_backtest(self):
        import os
        os.environ["UPBIT_ACCESS_KEY"] = "test"
        os.environ["UPBIT_SECRET_KEY"] = "test"
        from backtesting.backtester import Backtester

        backtester = Backtester()
        df = create_test_df(200)
        result = result = backtester.run(df, random_signal_fn, "KRW-BTC", 1_000_000)

        assert result is not None
        assert result.initial_capital == 1_000_000
        assert result.final_capital > 0
        assert result.market == "KRW-BTC"

    @pytest.mark.asyncio
    async def test_result_has_metrics(self):
        import os
        os.environ["UPBIT_ACCESS_KEY"] = "test"
        os.environ["UPBIT_SECRET_KEY"] = "test"
        from backtesting.backtester import Backtester

        backtester = Backtester()
        df = create_test_df(300)
        result = result = backtester.run(df, random_signal_fn, "KRW-ETH", 1_000_000)

        assert hasattr(result, "total_return")
        assert hasattr(result, "sharpe_ratio")
        assert hasattr(result, "max_drawdown")
        assert hasattr(result, "win_rate")
        assert 0 <= result.win_rate <= 100

    @pytest.mark.asyncio
    async def test_monte_carlo(self):
        import os
        os.environ["UPBIT_ACCESS_KEY"] = "test"
        os.environ["UPBIT_SECRET_KEY"] = "test"
        from backtesting.backtester import Backtester

        backtester = Backtester()
        df = create_test_df(200)
        result = result = backtester.run(df, random_signal_fn, "KRW-BTC", 1_000_000)

        if result.trades:
            mc = backtester.monte_carlo(result.trades, n_simulations=100)
            assert "mean_final" in mc
            assert "ruin_probability" in mc
            assert 0 <= mc["ruin_probability"] <= 100

    @pytest.mark.asyncio
    async def test_walk_forward(self):
        import os
        os.environ["UPBIT_ACCESS_KEY"] = "test"
        os.environ["UPBIT_SECRET_KEY"] = "test"
        from backtesting.backtester import Backtester

        backtester = Backtester()
        df = create_test_df(500)
        results = await backtester.walk_forward(df, "default", "KRW-BTC", n_splits=3)

        assert len(results) == 3
        for r in results:
            assert r.final_capital > 0

    @pytest.mark.asyncio
    async def test_no_trades_edge_case(self):
        import os
        os.environ["UPBIT_ACCESS_KEY"] = "test"
        os.environ["UPBIT_SECRET_KEY"] = "test"
        from backtesting.backtester import Backtester

        backtester = Backtester()
        df = create_test_df(100)
        # 신호 없는 전략
        no_signal_fn = lambda df: pd.Series(0, index=df.index)
        result = result = backtester.run(df, no_signal_fn, "KRW-BTC", 1_000_000)

        assert result.total_trades == 0
        assert result.final_capital == 1_000_000  # 손익 없음


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
