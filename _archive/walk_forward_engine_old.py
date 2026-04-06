"""
Apex Bot - Walk-Forward 검증 엔진 (M1-B)
6개월 학습 / 1개월 검증 롤링 윈도우
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Callable, Dict, Any
from datetime import datetime, timedelta
from loguru import logger
from .backtest_engine import BacktestEngine, BacktestConfig, BacktestResult


@dataclass
class WFConfig:
    train_months: int = 6
    test_months:  int = 1
    min_trades:   int = 10


@dataclass
class WFWindow:
    window_id:    int
    train_start:  datetime
    train_end:    datetime
    test_start:   datetime
    test_end:     datetime
    train_result: BacktestResult = None
    test_result:  BacktestResult = None


@dataclass
class WFReport:
    windows:         List[WFWindow] = field(default_factory=list)
    avg_return:      float = 0.0
    avg_sharpe:      float = 0.0
    avg_win_rate:    float = 0.0
    avg_mdd:         float = 0.0
    consistency:     float = 0.0
    is_robust:       bool  = False


class WalkForwardEngine:
    """Walk-Forward 검증 엔진"""

    def __init__(self, wf_config: WFConfig = None, bt_config: BacktestConfig = None):
        self.wf_cfg = wf_config or WFConfig()
        self.bt_cfg = bt_config or BacktestConfig()
        logger.info("✅ WalkForwardEngine 초기화")

    def run(
        self,
        df: pd.DataFrame,
        signal_fn: Callable[[pd.DataFrame], pd.Series],
        market: str = "KRW-BTC",
    ) -> WFReport:
        """
        Walk-Forward 전체 실행
        signal_fn: df를 받아 signals Series를 반환하는 함수
        """
        report  = WFReport()
        windows = self._split_windows(df)
        logger.info(f"Walk-Forward 시작: {len(windows)}개 윈도우")

        for w in windows:
            train_df = df[w.train_start:w.train_end]
            test_df  = df[w.test_start:w.test_end]

            if len(train_df) < 50 or len(test_df) < 5:
                continue

            engine = BacktestEngine(self.bt_cfg)

            train_sig  = signal_fn(train_df)
            w.train_result = engine.run(train_df, train_sig, market, "train")

            test_sig   = signal_fn(test_df)
            w.test_result  = engine.run(test_df, test_sig, market, "test")

            report.windows.append(w)
            logger.info(
                f"  윈도우 {w.window_id}: "
                f"학습 수익={w.train_result.total_return:.2%} | "
                f"검증 수익={w.test_result.total_return:.2%}"
            )

        report = self._calc_report(report)
        return report

    def _split_windows(self, df: pd.DataFrame) -> List[WFWindow]:
        windows   = []
        idx       = df.index
        start     = idx[0]
        end       = idx[-1]
        win_id    = 1
        train_td  = timedelta(days=self.wf_cfg.train_months * 30)
        test_td   = timedelta(days=self.wf_cfg.test_months  * 30)
        cur       = start

        while cur + train_td + test_td <= end:
            w = WFWindow(
                window_id   = win_id,
                train_start = cur,
                train_end   = cur + train_td,
                test_start  = cur + train_td,
                test_end    = cur + train_td + test_td,
            )
            windows.append(w)
            cur    += test_td
            win_id += 1

        return windows

    def _calc_report(self, report: WFReport) -> WFReport:
        test_results = [w.test_result for w in report.windows if w.test_result]
        if not test_results:
            return report

        report.avg_return   = float(np.mean([r.total_return  for r in test_results]))
        report.avg_sharpe   = float(np.mean([r.sharpe_ratio  for r in test_results]))
        report.avg_win_rate = float(np.mean([r.win_rate      for r in test_results]))
        report.avg_mdd      = float(np.mean([r.max_drawdown  for r in test_results]))
        profitable = sum(1 for r in test_results if r.total_return > 0)
        report.consistency  = profitable / len(test_results)
        report.is_robust    = (
            report.avg_sharpe   > 0.5
            and report.avg_mdd  > -0.15
            and report.consistency > 0.6
        )
        return report
