"""APEX BOT - Walk-Forward   v1.1
   → OOS  →  

 :
  v1.1 - f-string     (line 389)
          f-string → .format()"""
from __future__ import annotations

import json
import time
import asyncio
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from loguru import logger

try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    OPTUNA_OK = True
except ImportError:
    OPTUNA_OK = False
    logger.warning("Optuna  -> Walk-Forward   ")

PARAM_FILE = Path("config/optimized_params.json")
REPORT_DIR = Path("reports/walk_forward")


@dataclass
class WFResult:
    """Walk-Forward"""
    strategy_name:  str
    is_sample_days: int
    oos_sample_days: int
    best_params:    Dict
    is_sharpe:      float = 0.0
    oos_sharpe:     float = 0.0
    oos_win_rate:   float = 0.0
    oos_pnl_pct:    float = 0.0
    oos_max_dd:     float = 0.0
    is_profitable:  bool  = False
    weight_boost:   float = 1.0
    updated_at:     str   = field(
        default_factory=lambda: datetime.now().isoformat()
    )


class WalkForwardRunner:
    """Walk-Forward   

    :
      1. In-Sample (90): Optuna  
      2. Out-of-Sample (30):   
      3. OOS  >= 0.5 ->  / < 0.5 -> 
      4. config/optimized_params.json 
      5. engine.py"""

    STRATEGIES = [
        "MACD_Cross",
        "RSI_Divergence",
        "Supertrend",
        "Bollinger_Squeeze",
        "VWAP_Reversion",
        "VolBreakout",
        "ATR_Channel",
        "OrderBlock_SMC",
    ]

    def __init__(
        self,
        in_sample_days:  int = 90,
        out_sample_days: int = 30,
        n_trials:        int = 30,
        target_markets:  Optional[List[str]] = None,
    ):
        self.in_sample_days  = in_sample_days
        self.out_sample_days = out_sample_days
        self.n_trials        = n_trials
        self.target_markets  = target_markets or ["KRW-BTC", "KRW-ETH"]
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        PARAM_FILE.parent.mkdir(parents=True, exist_ok=True)

    # ── 메인 실행 ───────────────────────────────────────────────

    async def run_all_strategies(self) -> Dict[str, WFResult]:
        """Walk-Forward"""
        logger.info(
            "Walk-Forward  | "
            "IS={} | OOS={} | ={}/".format(
                self.in_sample_days, self.out_sample_days, self.n_trials
            )
        )

        dfs = await self._fetch_data()
        if not dfs:
            logger.error("Walk-Forward:   ")
            return {}

        results: Dict[str, WFResult] = {}
        for strategy_name in self.STRATEGIES:
            try:
                result = await self._run_strategy_wf(strategy_name, dfs)
                results[strategy_name] = result
                status = "적용" if result.is_profitable else "제외"
                logger.info(
                    "  {:<22s} | ={:+.3f} | ={:.1f}% | "
                    "PnL={:+.2f}% | {}".format(
                        strategy_name,
                        result.oos_sharpe,
                        result.oos_win_rate,
                        result.oos_pnl_pct,
                        status,
                    )
                )
            except Exception as e:
                logger.error("  {} WF : {}".format(strategy_name, e))

        self._save_report(results)
        return results

    # ── 데이터 수집 ─────────────────────────────────────────────

    async def _fetch_data(self) -> Dict[str, pd.DataFrame]:
        """REST API OHLCV"""
        from data.collectors.rest_collector import RestCollector
        from data.processors.candle_processor import CandleProcessor

        collector = RestCollector()
        processor = CandleProcessor()
        dfs: Dict[str, pd.DataFrame] = {}

        # [FIX] 500 하드캡 제거 → 인샘플+아웃샘플 전체 기간 조회
        # 업비트 API 최대 200개 제한으로 day 단위로 변경
        total_candles = min(
            self.in_sample_days + self.out_sample_days + 10,  # 여유 10일
            200,  # 업비트 일봉 최대 200개
        )
        _candle_type = "day"  # 시간봉 대신 일봉 사용

        for market in self.target_markets:
            try:
                df_raw = await collector.get_ohlcv(
                    market, _candle_type, total_candles
                )
                if df_raw is not None and len(df_raw) > 100:
                    df = await processor.process(market, df_raw, "60")
                    if df is not None and not df.empty:
                        dfs[market] = df
                        logger.debug(
                            " : {} | {}".format(market, len(df))
                        )
                await asyncio.sleep(0.3)
            except Exception as e:
                logger.warning("   ({}): {}".format(market, e))

        return dfs

    # ── 단일 전략 WF ────────────────────────────────────────────

    async def _run_strategy_wf(
        self, strategy_name: str, dfs: Dict[str, pd.DataFrame]
    ) -> WFResult:
        """Walk-Forward"""
        ratio = self.in_sample_days / (
            self.in_sample_days + self.out_sample_days
        )

        is_dfs, oos_dfs = {}, {}
        for market, df in dfs.items():
            split = int(len(df) * ratio)
            is_dfs[market]  = df.iloc[:split]
            oos_dfs[market] = df.iloc[split:]

        best_params = await self._optimize(strategy_name, is_dfs)
        is_metrics  = await self._evaluate(strategy_name, is_dfs,  best_params)
        oos_metrics = await self._evaluate(strategy_name, oos_dfs, best_params)

        is_profitable = (
            oos_metrics["sharpe"]   > 0.5
            and oos_metrics["win_rate"] > 50
            and oos_metrics["pnl_pct"]  > 0
        )

        if oos_metrics["sharpe"] >= 1.5:
            weight_boost = 1.5
        elif oos_metrics["sharpe"] >= 0.5:
            weight_boost = 1.0
        else:
            weight_boost = 0.0

        return WFResult(
            strategy_name   = strategy_name,
            is_sample_days  = self.in_sample_days,
            oos_sample_days = self.out_sample_days,
            best_params     = best_params,
            is_sharpe       = is_metrics["sharpe"],
            oos_sharpe      = oos_metrics["sharpe"],
            oos_win_rate    = oos_metrics["win_rate"],
            oos_pnl_pct     = oos_metrics["pnl_pct"],
            oos_max_dd      = oos_metrics["max_dd"],
            is_profitable   = is_profitable,
            weight_boost    = weight_boost,
        )

    # ── Optuna 최적화 ────────────────────────────────────────────

    async def _optimize(
        self, strategy_name: str, dfs: Dict[str, pd.DataFrame]
    ) -> Dict:
        """Optuna (asyncio 스코프 버그 수정)"""
        import functools  # asyncio는 상단 import 사용 (로컬 재선언 금지)
        if not OPTUNA_OK or not dfs:
            return self._default_params(strategy_name)

        # [FIX] objective는 순수 동기 함수 — asyncio/loop 참조 제거
        # _evaluate는 캐싱된 dfs만 사용하므로 동기 래퍼로 실행 가능
        def objective(trial: "optuna.Trial") -> float:
            params = self._suggest_params(trial, strategy_name)
            # 동기 환경에서 새 이벤트루프로 평가 실행
            _loop = asyncio.new_event_loop()
            try:
                metrics = _loop.run_until_complete(
                    self._evaluate(strategy_name, dfs, params)
                )
            finally:
                _loop.close()
            return -metrics["sharpe"]

        try:
            study = optuna.create_study(direction="minimize")
            # run_in_executor로 동기 Optuna 최적화를 별도 스레드에서 실행
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                functools.partial(
                    study.optimize,
                    objective,
                    n_trials=self.n_trials,
                    timeout=180,
                    show_progress_bar=False,
                )
            )
            return study.best_params
        except Exception as e:
            logger.debug("Optuna 최적화 실패 ({}): {}".format(strategy_name, e))
            return self._default_params(strategy_name)

    # ── 성과 평가 ────────────────────────────────────────────────

    async def _evaluate(
        self,
        strategy_name: str,
        dfs: Dict[str, pd.DataFrame],
        params: Dict,
    ) -> Dict:
        """( )"""
        all_returns: List[float] = []

        try:
            strategy = self._load_strategy(strategy_name, params)
            if strategy is None:
                return {
                    "sharpe": 0.0, "win_rate": 50.0,
                    "pnl_pct": 0.0, "max_dd": 0.0,
                }

            from strategies.base_strategy import SignalType

            for market, df in dfs.items():
                if len(df) < 50:
                    continue
                for i in range(50, len(df) - 5):
                    sub = df.iloc[:i]
                    sig = strategy.generate_signal(sub, market)
                    if sig is None:
                        continue
                    if sig.signal == SignalType.BUY:
                        future_ret = (
                            df["close"].iloc[i + 5]
                            - df["close"].iloc[i]
                        ) / df["close"].iloc[i] * 100
                        all_returns.append(float(future_ret))

        except Exception as e:
            logger.debug(
                "  ({}): {}".format(strategy_name, e)
            )

        if not all_returns:
            return {
                "sharpe": 0.0, "win_rate": 50.0,
                "pnl_pct": 0.0, "max_dd": 0.0,
            }

        arr      = np.array(all_returns)
        wins     = arr[arr > 0]
        win_rate = float(len(wins) / len(arr) * 100) if arr.size > 0 else 50.0
        sharpe   = float(
            arr.mean() / (arr.std() + 1e-10) * np.sqrt(252)
        )
        pnl_pct  = float(arr.mean())

        cum    = np.cumsum(arr)
        peak   = np.maximum.accumulate(cum)
        dd_arr = cum - peak
        max_dd = float(abs(dd_arr.min())) if dd_arr.size > 0 else 0.0

        return {
            "sharpe":   round(sharpe,   3),
            "win_rate": round(win_rate, 1),
            "pnl_pct":  round(pnl_pct,  2),
            "max_dd":   round(max_dd,   2),
        }

    # ── 전략 동적 로드 ───────────────────────────────────────────

    def _load_strategy(self, strategy_name: str, params: Dict):
        """_load_strategy 실행"""
        mapping = {
            "MACD_Cross": (
                "strategies.momentum.macd_cross",
                "MACDCrossStrategy",
            ),
            "RSI_Divergence": (
                "strategies.momentum.rsi_divergence",
                "RSIDivergenceStrategy",
            ),
            "Supertrend": (
                "strategies.momentum.supertrend",
                "SupertrendStrategy",
            ),
            "Bollinger_Squeeze": (
                "strategies.mean_reversion.bollinger_squeeze",
                "BollingerSqueezeStrategy",
            ),
            "VWAP_Reversion": (
                "strategies.mean_reversion.vwap_reversion",
                "VWAPReversionStrategy",
            ),
            "VolBreakout": (
                "strategies.volatility.vol_breakout",
                "VolBreakoutStrategy",
            ),
            "ATR_Channel": (
                "strategies.volatility.atr_channel",
                "ATRChannelStrategy",
            ),
            "OrderBlock_SMC": (
                "strategies.market_structure.order_block",
                "OrderBlockStrategy",
            ),
        }

        if strategy_name not in mapping:
            return None

        module_path, class_name = mapping[strategy_name]
        try:
            import importlib
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            return cls(params=params)
        except Exception as e:
            logger.debug(
                "   ({}): {}".format(strategy_name, e)
            )
            return None

    # ── 파라미터 제안 ────────────────────────────────────────────

    def _suggest_params(self, trial, strategy_name: str) -> Dict:
        """Optuna"""
        mapping = {
            "MACD_Cross": {
                "fast":   trial.suggest_int("fast",   8, 16),
                "slow":   trial.suggest_int("slow",   20, 32),
                "signal": trial.suggest_int("signal", 7, 12),
            },
            "RSI_Divergence": {
                "rsi_period": trial.suggest_int("rsi_period", 10, 20),
                "oversold":   trial.suggest_int("oversold",   25, 40),
                "overbought": trial.suggest_int("overbought", 60, 80),
            },
            "Supertrend": {
                "atr_period": trial.suggest_int("atr_period",    7, 14),
                "multiplier": trial.suggest_float("multiplier",  2.0, 4.0),
                "min_adx":    trial.suggest_int("min_adx",      20, 35),
            },
            "Bollinger_Squeeze": {
                "bb_period": trial.suggest_int("bb_period",    15, 25),
                "bb_std":    trial.suggest_float("bb_std",     1.5, 2.5),
                "kc_mult":   trial.suggest_float("kc_mult",    1.0, 2.0),
            },
            "VWAP_Reversion": {
                "vwap_dev_buy":  trial.suggest_float(
                    "vwap_dev_buy",  -0.03, -0.005
                ),
                "vwap_dev_sell": trial.suggest_float(
                    "vwap_dev_sell",  0.005,  0.03
                ),
                "rsi_oversold": trial.suggest_int("rsi_oversold", 25, 45),
            },
            "VolBreakout": {
                "k":                trial.suggest_float("k", 0.3, 0.7),
                "volume_threshold": trial.suggest_float(
                    "volume_threshold", 1.0, 2.0
                ),
            },
            "ATR_Channel": {
                "ema_period":     trial.suggest_int("ema_period",      15, 25),
                "atr_multiplier": trial.suggest_float(
                    "atr_multiplier", 1.5, 3.0
                ),
                "min_adx":        trial.suggest_int("min_adx",         15, 30),
            },
            "OrderBlock_SMC": {
                "ob_lookback":  trial.suggest_int("ob_lookback",    10, 30),
                "fvg_min_size": trial.suggest_float(
                    "fvg_min_size", 0.001, 0.01
                ),
            },
        }
        return mapping.get(strategy_name, {})

    def _default_params(self, strategy_name: str) -> Dict:
        """(Optuna   fallback)"""
        defaults = {
            "MACD_Cross": {
                "fast": 12, "slow": 26, "signal": 9,
            },
            "RSI_Divergence": {
                "rsi_period": 14, "oversold": 35, "overbought": 65,
            },
            "Supertrend": {
                "atr_period": 10, "multiplier": 3.0, "min_adx": 25,
            },
            "Bollinger_Squeeze": {
                "bb_period": 20, "bb_std": 2.0, "kc_mult": 1.5,
            },
            "VWAP_Reversion": {
                "vwap_dev_buy": -0.015,
                "vwap_dev_sell": 0.015,
                "rsi_oversold": 35,
            },
            "VolBreakout": {
                "k": 0.5, "volume_threshold": 1.2,
            },
            "ATR_Channel": {
                "ema_period": 20, "atr_multiplier": 2.0, "min_adx": 20,
            },
            "OrderBlock_SMC": {
                "ob_lookback": 20, "fvg_min_size": 0.003,
            },
        }
        return defaults.get(strategy_name, {})

    # ── 파라미터 저장/로드 ───────────────────────────────────────

    def apply_best_params(self, results: Dict[str, WFResult]):
        """JSON"""
        output = {
            "updated_at":      datetime.now().isoformat(),
            "in_sample_days":  self.in_sample_days,
            "out_sample_days": self.out_sample_days,
            "strategies":      {},
        }

        for name, result in results.items():
            output["strategies"][name] = {
                "params":       result.best_params,
                "weight_boost": result.weight_boost,
                "oos_sharpe":   result.oos_sharpe,
                "oos_win_rate": result.oos_win_rate,
                "oos_pnl_pct":  result.oos_pnl_pct,
                "is_active":    result.is_profitable,
                "updated_at":   result.updated_at,
            }

        PARAM_FILE.write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        active   = sum(1 for r in results.values() if r.is_profitable)
        inactive = len(results) - active
        logger.success(
            "Walk-Forward 파라미터 저장: {} | "
            "활성={}개 | 비활성={}개".format(PARAM_FILE, active, inactive)
        )

    @staticmethod
    def load_optimized_params() -> Optional[Dict]:
        """config/optimized_params.json → strategies dict 반환"""
        if not PARAM_FILE.exists():
            return None
        try:
            data    = json.loads(PARAM_FILE.read_text(encoding="utf-8"))
            # [FIX] strategies 키가 있으면 그 하위 딕셔너리만 반환
            if "strategies" in data:
                return data["strategies"]
            updated = data.get("updated_at", "2000-01-01")
            try:
                age = (datetime.now() - datetime.fromisoformat(updated)).days
            except Exception:
                age = 0
            if age > 14:
                logger.warning(
                    "Walk-Forward  {}   "
                    "->  ".format(age)
                )
            return data.get("strategies", {})
        except Exception as e:
            logger.error("  : {}".format(e))
            return None

    # ── 리포트 저장 ─────────────────────────────────────────────

    def _save_report(self, results: Dict[str, WFResult]):
        """Walk-Forward  JSON"""
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = REPORT_DIR / ("wf_report_" + ts + ".json")
        data = {
            "generated_at": datetime.now().isoformat(),
            "results": {k: asdict(v) for k, v in results.items()},
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Walk-Forward : {}".format(path))


# ── 엔진 스케줄러에서 호출하는 래퍼 ────────────────────────────

async def run_weekly_walk_forward() -> Dict[str, WFResult]:
    """02:00"""
    from config.settings import get_settings
    settings = get_settings()

    runner = WalkForwardRunner(
        in_sample_days  = 90,
        out_sample_days = 30,
        n_trials        = 30,
        target_markets  = settings.trading.target_markets[:3],
    )
    results = await runner.run_all_strategies()
    runner.apply_best_params(results)
    return results
