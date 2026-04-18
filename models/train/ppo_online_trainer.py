
# models/train/ppo_online_trainer.py
"""
APEX BOT - PPO 온라인 학습 모듈
페이퍼 거래 결과를 experience buffer에 쌓고
주기적으로 PPO 정책을 업데이트합니다.
"""
import asyncio
from pathlib import Path
from typing import List, Dict, Optional
from loguru import logger
import numpy as np


class PPOOnlineTrainer:
    """
    PPO 온라인 학습 관리자
    
    동작 방식:
    1. 매 거래 결과를 experience_buffer에 저장
    2. buffer가 MIN_EXPERIENCES 이상 쌓이면 학습 트리거
    3. 주간 스케줄로 강제 재학습 실행
    4. 새 모델이 기존보다 나쁘면 자동 롤백
    """

    MODEL_PATH     = Path("models/saved/ppo/best_model.zip")
    BACKUP_PATH    = Path("models/saved/ppo/best_model_backup.zip")
    MIN_EXPERIENCES = 50    # 최소 경험 수집 후 학습
    MAX_BUFFER      = 200   # [FIX] 1000->200 조기학습 (하루 15회 거래 기준 ~2주)
    MIN_VALID_DATE  = "2026-04-16T21:37:00"  # [FIX] 수정후 정상 데이터만 학습
    RETRAIN_EPISODES = 200  # PPO 재학습 에피소드 수

    def __init__(self):
        self._buffer: List[Dict] = []
        self._total_experiences: int = 0
        self._retrain_count: int = 0
        self._is_training: bool = False
        self._last_reward: float = 0.0
        logger.info("✅ PPOOnlineTrainer 초기화 | "
                    f"min_exp={self.MIN_EXPERIENCES} | "
                    f"max_buf={self.MAX_BUFFER}")

    def add_experience(
        self,
        market: str,
        action: int,           # 0=HOLD, 1=BUY, 2=SELL
        profit_rate: float,    # 실현 수익률
        hold_hours: float,     # 보유 시간
        features: Optional[np.ndarray] = None,
    ):
        """거래 결과를 experience buffer에 추가"""
        # [FIX] profit_rate 단위 정규화: 소수(<0.1)로 들어오면 *100
        if abs(profit_rate) < 0.1 and profit_rate != 0:
            profit_rate = profit_rate * 100
        # 보상 계산: 수익률(%) - 수수료(0.05%) - 장기보유 페널티
        fee_penalty   = 0.05   # [FIX] 0.0005% → 0.05% (% 단위 기준)
        time_penalty  = max(0, hold_hours - 24) * 0.01  # [FIX] % 단위 기준
        reward = profit_rate - fee_penalty - time_penalty

        experience = {
            "market":      market,
            "action":      action,
            "profit_rate": profit_rate,
            "hold_hours":  hold_hours,
            "reward":      reward,
            "features":    features,
        }
        self._buffer.append(experience)
        self._total_experiences += 1

        # buffer 크기 제한
        if len(self._buffer) > self.MAX_BUFFER:
            self._buffer.pop(0)

        logger.debug(
            f"[PPOOnline] 경험 추가 ({market}): "
            f"action={action} reward={reward:.4f} | "
            f"buffer={len(self._buffer)}/{self.MAX_BUFFER}"
        )

    async def train_if_ready(self) -> bool:
        """buffer가 충분하면 PPO 재학습 실행"""
        if self._is_training:
            logger.warning("[PPOOnline] 이미 학습 중 — 스킵")
            return False

        if len(self._buffer) < self.MIN_EXPERIENCES:
            logger.debug(
                f"[PPOOnline] 경험 부족 "
                f"({len(self._buffer)}/{self.MIN_EXPERIENCES}) — 스킵"
            )
            return False

        self._is_training = True
        try:
            return await self._run_ppo_retrain()
        finally:
            self._is_training = False

    async def _run_ppo_retrain(self) -> bool:
        """PPO 재학습 실행"""
        logger.info(
            f"[PPOOnline] 🔄 PPO 재학습 시작 | "
            f"경험={len(self._buffer)}개"
        )
        try:
            # 백업
            if self.MODEL_PATH.exists():
                import shutil
                shutil.copy(self.MODEL_PATH, self.BACKUP_PATH)
                logger.info("[PPOOnline] 기존 모델 백업 완료")

            # 재학습 스크립트 실행
            import sys
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "run_ppo_train.py",
                "--episodes", str(self.RETRAIN_EPISODES),
                "--buffer-size", str(len(self._buffer)),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=1800
            )

            if proc.returncode != 0:
                err = stderr.decode(errors="replace")[-300:]
                logger.error(f"[PPOOnline] 재학습 실패: {err}")
                self._rollback()
                return False

            self._retrain_count += 1
            self._buffer.clear()  # buffer 초기화
            logger.info(
                f"[PPOOnline] ✅ PPO 재학습 완료 "
                f"(총 {self._retrain_count}회)"
            )
            return True

        except asyncio.TimeoutError:
            logger.error("[PPOOnline] 타임아웃 — 롤백")
            self._rollback()
            return False
        except Exception as e:
            logger.error(f"[PPOOnline] 예외: {e}")
            self._rollback()
            return False

    def _rollback(self):
        if self.BACKUP_PATH.exists():
            import shutil
            shutil.copy(self.BACKUP_PATH, self.MODEL_PATH)
            logger.info("[PPOOnline] 🔄 PPO 이전 모델 복원")

    def get_status(self) -> dict:
        return {
            "buffer_size":       len(self._buffer),
            "total_experiences": self._total_experiences,
            "retrain_count":     self._retrain_count,
            "is_training":       self._is_training,
            "ready_to_train":    len(self._buffer) >= self.MIN_EXPERIENCES,
        }

    def get_buffer_stats(self) -> dict:
        """buffer 통계"""
        if not self._buffer:
            return {"count": 0}
        rewards = [e["reward"] for e in self._buffer]
        profits = [e["profit_rate"] for e in self._buffer]
        return {
            "count":       len(self._buffer),
            "avg_reward":  float(np.mean(rewards)),
            "avg_profit":  float(np.mean(profits)),
            "win_rate":    float(np.mean([p > 0 for p in profits])),
            "best_trade":  float(np.max(profits)),
            "worst_trade": float(np.min(profits)),
        }
