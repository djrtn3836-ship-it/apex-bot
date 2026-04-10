"""APEX BOT -   
Optuna    + Walk-Forward"""
import asyncio
from typing import Dict, Optional, Callable, Any
from loguru import logger

try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    OPTUNA_OK = True
except ImportError:
    OPTUNA_OK = False
    logger.warning("Optuna  -  ")

import pandas as pd
import numpy as np

from backtesting.backtester import Backtester
from config.settings import get_settings


class StrategyOptimizer:
    """Optuna    

     :    (  )
    : TPE Sampler + Pruner ( trial  )
    : Walk-Forward ( )"""

    OPTIMIZATION_METRIC = "sharpe_ratio"  # 최적화 지표

    def __init__(self):
        self.settings = get_settings()
        self.backtester = Backtester()

    async def optimize(
        self,
        df: pd.DataFrame,
        strategy_class,
        param_space: Dict[str, Any],
        market: str = "KRW-BTC",
        n_trials: int = 100,
        n_jobs: int = 1,
        timeout: int = 3600,
    ) -> Dict:
        """Args:
            df: OHLCV +  DataFrame
            strategy_class:   
            param_space: {param_name: (type, min, max) or [choices]}
            n_trials: Optuna  
            timeout:   ()

        Returns:
            best_params:"""
        if not OPTUNA_OK:
            logger.error("Optuna  -  ")
            return {}

        logger.info(f"   | {strategy_class.__name__} | {n_trials} ")

        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=42),
            pruner=optuna.pruners.MedianPruner(n_startup_trials=10),
        )

        # 비동기 목적 함수 래핑
        def objective(trial: optuna.Trial) -> float:
            params = self._sample_params(trial, param_space)
            try:
                # 전략 인스턴스 생성 및 파라미터 설정
                strategy = strategy_class()
                for k, v in params.items():
                    if hasattr(strategy, k):
                        setattr(strategy, k, v)

                # 신호 함수 생성
                signal_fn = self._create_signal_fn(strategy, market)

                # 동기 백테스트 실행
                loop = asyncio.new_event_loop()
                result = loop.run_until_complete(
                    self.backtester.run(df, signal_fn, market, 1_000_000)
                )
                loop.close()

                if result.total_trades < 5:
                    return -100.0  # 거래 없으면 패널티

                # 목적 함수: 샤프비율 - 드로다운 패널티
                score = result.sharpe_ratio - result.max_drawdown * 0.1
                logger.debug(f"Trial {trial.number}: {params} → score={score:.3f}")
                return float(score)

            except Exception as e:
                logger.error(f"Trial : {e}")
                return -999.0

        # 최적화 실행 (별도 스레드)
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: study.optimize(
                objective, n_trials=n_trials, timeout=timeout, n_jobs=n_jobs, show_progress_bar=False
            )
        )

        best_params = study.best_params
        best_value = study.best_value

        logger.info(f"   |  ={best_value:.3f}")
        logger.info(f"    : {best_params}")

        # Walk-Forward 검증
        if len(df) > 500:
            wf_score = await self._walk_forward_validate(
                df, strategy_class, best_params, market
            )
            logger.info(f"   Walk-Forward  : {wf_score:.3f}")
            if abs(wf_score - best_value) > best_value * 0.5:
                logger.warning("  : Walk-Forward   50% ")

        return {
            "best_params": best_params,
            "best_score": best_value,
            "n_trials": len(study.trials),
            "strategy": strategy_class.__name__,
            "market": market,
        }

    def _sample_params(self, trial: "optuna.Trial", param_space: Dict) -> Dict:
        """Optuna trial"""
        params = {}
        for name, spec in param_space.items():
            if isinstance(spec, list):
                params[name] = trial.suggest_categorical(name, spec)
            elif isinstance(spec, tuple) and len(spec) == 3:
                ptype, low, high = spec
                if ptype == "int":
                    params[name] = trial.suggest_int(name, low, high)
                elif ptype == "float":
                    params[name] = trial.suggest_float(name, low, high)
                elif ptype == "log_float":
                    params[name] = trial.suggest_float(name, low, high, log=True)
        return params

    def _create_signal_fn(self, strategy, market: str) -> Callable:
        """docstring"""
        from strategies.base_strategy import SignalType

        def signal_fn(df: pd.DataFrame) -> pd.Series:
            signals = pd.Series(0, index=df.index)
            for i in range(30, len(df)):
                sub_df = df.iloc[:i+1]
                sig = strategy.analyze(market, sub_df)
                if sig is None:
                    continue
                if sig.signal_type == SignalType.BUY:
                    signals.iloc[i] = 1
                elif sig.signal_type == SignalType.SELL:
                    signals.iloc[i] = -1
            return signals

        return signal_fn

    async def _walk_forward_validate(
        self,
        df: pd.DataFrame,
        strategy_class,
        params: Dict,
        market: str,
        n_splits: int = 3,
    ) -> float:
        """Walk-Forward"""
        strategy = strategy_class()
        for k, v in params.items():
            if hasattr(strategy, k):
                setattr(strategy, k, v)

        signal_fn = self._create_signal_fn(strategy, market)
        results = await self.backtester.walk_forward(df, signal_fn, market, n_splits)
        scores = [r.sharpe_ratio - r.max_drawdown * 0.1 for r in results]
        return float(np.mean(scores))

    # ── 사전 정의 파라미터 공간 ───────────────────────────────────
    @staticmethod
    def get_macd_param_space() -> Dict:
        return {
            "fast_period": ("int", 8, 16),
            "slow_period": ("int", 20, 30),
            "signal_period": ("int", 7, 12),
            "min_hist": ("float", 0.0001, 0.005),
        }

    @staticmethod
    def get_rsi_param_space() -> Dict:
        return {
            "rsi_period": ("int", 10, 21),
            "oversold": ("int", 25, 40),
            "overbought": ("int", 60, 75),
        }

    @staticmethod
    def get_bollinger_param_space() -> Dict:
        return {
            "bb_period": ("int", 15, 25),
            "bb_std": ("float", 1.5, 2.5),
            "squeeze_threshold": ("float", 0.01, 0.05),
        }
