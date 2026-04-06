"""
Apex Bot - Optuna 하이퍼파라미터 최적화 (M1-D)
"""
from dataclasses import dataclass
from typing import Dict, Any, Callable, Optional
from loguru import logger

try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    OPTUNA_OK = True
except ImportError:
    OPTUNA_OK = False
    logger.warning("optuna 미설치 — pip install optuna")


@dataclass
class OptimResult:
    best_params:  Dict[str, Any]
    best_value:   float
    n_trials:     int
    study_name:   str


class ParamOptimizer:
    """Optuna 기반 전략 파라미터 최적화"""

    def __init__(self, n_trials: int = 100, direction: str = "maximize"):
        self.n_trials  = n_trials
        self.direction = direction
        logger.info(f"✅ ParamOptimizer 초기화 ({n_trials}회 탐색)")

    def optimize(
        self,
        objective_fn: Callable,
        study_name: str = "apex_strategy",
        timeout: Optional[int] = 300,
    ) -> Optional[OptimResult]:
        """
        objective_fn(trial) → float (샤프비율 등 최대화할 지표)
        """
        if not OPTUNA_OK:
            logger.error("optuna 미설치 — 최적화 불가")
            return None

        study = optuna.create_study(
            direction  = self.direction,
            study_name = study_name,
        )
        study.optimize(objective_fn, n_trials=self.n_trials, timeout=timeout)

        result = OptimResult(
            best_params = study.best_params,
            best_value  = study.best_value,
            n_trials    = len(study.trials),
            study_name  = study_name,
        )
        logger.info(
            f"최적화 완료: 최적값={result.best_value:.4f} | "
            f"파라미터={result.best_params}"
        )
        return result

    def get_param_space_example(self, trial) -> Dict[str, Any]:
        """파라미터 탐색 공간 예시 (objective_fn 작성 참고용)"""
        return {
            "stop_loss_pct":    trial.suggest_float("stop_loss_pct",    0.01, 0.05),
            "take_profit_pct":  trial.suggest_float("take_profit_pct",  0.02, 0.10),
            "position_size_pct":trial.suggest_float("position_size_pct",0.05, 0.20),
            "rsi_period":       trial.suggest_int(  "rsi_period",       7,    21   ),
            "atr_multiplier":   trial.suggest_float("atr_multiplier",   1.5,  3.5  ),
        }
