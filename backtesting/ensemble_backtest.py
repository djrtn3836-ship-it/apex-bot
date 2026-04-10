"""APEX BOT -  
       .
 : N   K    BUY/SELL"""
import asyncio
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple
from dataclasses import dataclass, field
from loguru import logger

from backtesting.backtester import Backtester, BacktestResult, Trade
from backtesting.signal_generator import STRATEGIES, get_signals
from backtesting.data_loader import fetch_ohlcv


@dataclass
class EnsembleConfig:
    """docstring"""
    strategies:      List[str] = field(default_factory=list)   # 사용할 전략 목록
    min_agree:       int   = 2       # 최소 동의 전략 수 (이 이상일 때만 진입)
    min_agree_sell:  int   = 1       # 매도 최소 동의 수 (매도는 보수적으로)
    score_weighted:  bool  = True    # True: 가중 점수 방식, False: 단순 투표
    weights:         Dict[str, float] = field(default_factory=dict)  # 전략별 가중치


class EnsembleBacktester:
    """-     
    -     →     
    -     (     )"""

    # 기본 전략 조합 (단독 테스트에서 양수 샤프 기록한 전략들)
    DEFAULT_STRATEGIES = [
        "ml_strategy",
        "trend_following",
        "macd_momentum",
    ]

    # 모든 8개 전략 조합
    ALL_STRATEGIES = list(STRATEGIES.keys())

    def __init__(self, base_backtester: Backtester = None):
        self.bt = base_backtester or Backtester()

    def build_ensemble_signal(
        self,
        df:          pd.DataFrame,
        strategies:  List[str],
        weights:     Dict[str, float] = None,
        min_agree:   int = 2,
    ) -> Tuple[pd.Series, pd.DataFrame]:
        """Returns:
            (ensemble_signal, signal_df)
            - ensemble_signal: +1/-1/0 pd.Series
            - signal_df:    DataFrame ()"""
        signal_df = pd.DataFrame(index=df.index)

        for name in strategies:
            try:
                sig = get_signals(name, df)
                signal_df[name] = sig
            except Exception as e:
                logger.warning(f" {name}   : {e}")
                signal_df[name] = pd.Series(0, index=df.index)

        # 가중치 설정 (없으면 동일 가중치)
        if weights is None:
            weights = {s: 1.0 for s in strategies}

        # 가중 점수 계산
        buy_score  = pd.Series(0.0, index=df.index)
        sell_score = pd.Series(0.0, index=df.index)

        for name in strategies:
            w = weights.get(name, 1.0)
            buy_score  += (signal_df[name] == 1).astype(float) * w
            sell_score += (signal_df[name] == -1).astype(float) * w

        # 총 가중치 합
        total_weight = sum(weights.get(s, 1.0) for s in strategies)
        threshold    = (min_agree / len(strategies)) * total_weight

        ensemble = pd.Series(0, index=df.index)
        ensemble[buy_score  >= threshold] = 1
        ensemble[sell_score >= threshold] = -1

        logger.info(
            f"   | BUY: {(ensemble==1).sum()}회 "
            f"SELL: {(ensemble==-1).sum()}회 "
            f"HOLD: {(ensemble==0).sum()}회"
        )

        return ensemble, signal_df

    def run(
        self,
        df:         pd.DataFrame,
        strategies: List[str] = None,
        market:     str = "KRW-BTC",
        min_agree:  int = 2,
        weights:    Dict[str, float] = None,
    ) -> BacktestResult:
        """docstring"""
        if strategies is None:
            strategies = self.DEFAULT_STRATEGIES

        logger.info(f" : {strategies} | min_agree={min_agree}")
        ensemble_sig, sig_df = self.build_ensemble_signal(df, strategies, weights, min_agree)

        # 개별 전략 신호 분포 출력
        print("\n    :")
        for col in sig_df.columns:
            buys  = (sig_df[col] == 1).sum()
            sells = (sig_df[col] == -1).sum()
            print(f"    {col:<25} BUY:{buys:>3}  SELL:{sells:>3}")

        result = self.bt._simulate(df, ensemble_sig, f"ensemble_{len(strategies)}s_agree{min_agree}", market)
        return result

    def compare_agree_levels(
        self,
        df:         pd.DataFrame,
        strategies: List[str] = None,
        market:     str = "KRW-BTC",
    ) -> Dict[int, BacktestResult]:
        """min_agree (1~N)  
            ."""
        if strategies is None:
            strategies = self.DEFAULT_STRATEGIES

        results = {}
        print(f"\n{'='*60}")
        print(f"   min_agree    ({market})")
        print(f"  : {strategies}")
        print(f"{'='*60}")
        print(f"  {'agree':>6} {'':>8} {'':>7} {'':>7} {'':>8} {'':>6}")
        print(f"{'-'*60}")

        for k in range(1, len(strategies) + 1):
            r = self.run(df, strategies, market, min_agree=k)
            results[k] = r
            print(
                f"  agree≥{k}  "
                f"{r.total_return:>7.1f}%  "
                f"{r.sharpe_ratio:>7.3f}  "
                f"{r.win_rate:>6.1f}%  "
                f"{r.max_drawdown:>7.1f}%  "
                f"{r.total_trades:>6}"
            )

        print(f"{'='*60}")
        # 최적 agree 레벨 (Sharpe 기준)
        best_k = max(results, key=lambda k: results[k].sharpe_ratio)
        print(f"    agree : {best_k} ( {results[best_k].sharpe_ratio:.3f})")
        return results

    async def run_multi_coin(
        self,
        coins:      List[str],
        strategies: List[str] = None,
        interval:   str = "1d",
        days:       int = 365,
        min_agree:  int = 2,
    ) -> Dict[str, BacktestResult]:
        """docstring"""
        if strategies is None:
            strategies = self.DEFAULT_STRATEGIES

        results = {}
        for coin in coins:
            logger.info(f"[{coin}]   ...")
            df = await fetch_ohlcv(coin, interval, days)
            if df.empty:
                logger.warning(f"{coin}  , ")
                continue
            r = self.run(df, strategies, coin, min_agree)
            results[coin] = r

        # 코인별 비교 출력
        print(f"\n{'='*65}")
        print(f"     (: {strategies}, agree≥{min_agree})")
        print(f"{'='*65}")
        print(f"  {'':<12} {'':>8} {'':>7} {'':>7} {'':>8} {'':>6}")
        print(f"{'-'*65}")
        for coin, r in sorted(results.items(), key=lambda x: x[1].sharpe_ratio, reverse=True):
            print(
                f"  {coin:<12} "
                f"{r.total_return:>7.1f}%  "
                f"{r.sharpe_ratio:>7.3f}  "
                f"{r.win_rate:>6.1f}%  "
                f"{r.max_drawdown:>7.1f}%  "
                f"{r.total_trades:>6}"
            )
        print(f"{'='*65}")
        return results
