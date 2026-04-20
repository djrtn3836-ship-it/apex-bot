# models/train/auto_trainer.py — 자동 재학습 스케줄러
"""
매일 자동 재학습 파이프라인
- 데이터: 500개 캔들 (약 3주치 60분봉)
- 조건: 이전 모델 대비 val_acc 하락 없거나 RETRAIN_DAYS 경과
- 저장: models/saved/ensemble_best.pt (자동 교체)
- 실패시 이전 모델 자동 복원 (rollback)
"""

import asyncio
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional   # ✅ FIX: Optional 임포트 추가

from utils.logger import logger


class AutoTrainer:
    """자동 재학습 파이프라인"""

    MODEL_PATH   = Path("models/saved/ensemble_best.pt")
    BACKUP_PATH  = Path("models/saved/ensemble_backup.pt")
    MIN_IMPROVE  = 0.005    # val_acc 최소 향상 허용폭 (0.5%)
    RETRAIN_DAYS = 3        # 강제 재학습 주기 (일) — v4 적용 후 3일
    TRAIN_SCRIPT = "train_retrain.py"
    TIMEOUT_SEC  = 2400     # 최대 학습 시간 40분 (v4 증가)

    def __init__(self):
        # [FIX] 봇 재시작 시 모델 파일 mtime으로 _last_retrain 복원
        _mtime = None
        if self.MODEL_PATH.exists():
            from datetime import datetime as _dt
            _mtime = _dt.fromtimestamp(self.MODEL_PATH.stat().st_mtime)
        self._last_retrain: Optional[datetime] = _mtime
        self._last_val_acc: float = 0.0
        self._retrain_count: int  = 0
        self._is_training: bool   = False  # 중복 실행 방지

    async def run_if_needed(self) -> bool:
        """
        재학습 필요 여부 확인 후 실행
        Returns: True if retrained successfully
        """
        if self._is_training:
            logger.warning("[AutoTrainer] 이미 학습 중 — 스킵")
            return False

        if not self._should_retrain():
            return False

        self._is_training = True
        try:
            logger.info("[AutoTrainer] 🔄 자동 재학습 시작...")
            return await self._retrain()
        finally:
            self._is_training = False

    def _should_retrain(self) -> bool:
        """재학습 필요 여부 판단"""
        now = datetime.now()

        # 첫 실행 (모델 없을 때)
        if self._last_retrain is None:
            if not self.MODEL_PATH.exists():
                logger.info("[AutoTrainer] 저장된 모델 없음 → 초기 학습")
                return True
            # 모델은 있지만 이번 세션에서 아직 학습 안 함 → 스킵
            return False

        # 강제 재학습 주기 확인
        elapsed_days = (now - self._last_retrain).days
        if elapsed_days >= self.RETRAIN_DAYS:
            logger.info(
                f"[AutoTrainer] {elapsed_days}일 경과 "
                f"(주기: {self.RETRAIN_DAYS}일) → 강제 재학습"
            )
            return True

        return False

    async def _retrain(self) -> bool:
        """실제 재학습 프로세스 실행"""
        import sys
        try:
            # ── 기존 모델 백업 ────────────────────────────────
            if self.MODEL_PATH.exists():
                shutil.copy(self.MODEL_PATH, self.BACKUP_PATH)
                logger.info(f"[AutoTrainer] 백업 완료: {self.BACKUP_PATH}")

            # ── 별도 프로세스로 학습 실행 ─────────────────────
            proc = await asyncio.create_subprocess_exec(
                sys.executable, self.TRAIN_SCRIPT,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.TIMEOUT_SEC
            )

            if proc.returncode != 0:
                err_msg = stderr.decode(errors="replace")[-500:]
                logger.error(f"[AutoTrainer] 재학습 실패 (returncode={proc.returncode}): {err_msg}")
                self._rollback()
                return False

            # ── 결과 파싱 ─────────────────────────────────────
            output  = stdout.decode(errors="replace")
            new_acc = self._parse_val_acc(output)
            logger.info(f"[AutoTrainer] 학습 완료 — val_acc={new_acc:.4f}")

            # ── 성능 비교: 하락 시 롤백 ───────────────────────
            if (
                self._last_val_acc > 0
                and new_acc < self._last_val_acc - self.MIN_IMPROVE
            ):
                logger.warning(
                    f"[AutoTrainer] 정확도 하락 감지 "
                    f"({self._last_val_acc:.4f} → {new_acc:.4f}) "
                    f"→ 이전 모델로 롤백"
                )
                self._rollback()
                return False

            # ── 성공 기록 ─────────────────────────────────────
            self._last_retrain  = datetime.now()
            self._last_val_acc  = new_acc
            self._retrain_count += 1
            logger.info(
                f"[AutoTrainer] ✅ 재학습 성공 "
                f"val_acc={new_acc:.4f} "
                f"(총 {self._retrain_count}회)"
            )
            return True

        except asyncio.TimeoutError:
            logger.error(
                f"[AutoTrainer] 타임아웃 ({self.TIMEOUT_SEC//60}분 초과) — 롤백"
            )
            self._rollback()
            return False
        except Exception as e:
            logger.error(f"[AutoTrainer] 예외 발생: {e}")
            self._rollback()
            return False

    def _rollback(self):
        """이전 모델 복원"""
        if self.BACKUP_PATH.exists():
            shutil.copy(self.BACKUP_PATH, self.MODEL_PATH)
            logger.info("[AutoTrainer] 🔄 이전 모델 복원 완료")

    @staticmethod
    def _parse_val_acc(output: str) -> float:
        """학습 출력 로그에서 val_acc 파싱"""
        import re
        # "Val Acc: 61.43%" 또는 "val_acc=0.6143" 형식 모두 처리
        patterns = [
            r"Val Acc[:\s]+([\d.]+)%",      # 퍼센트 형식
            r"val_acc[=:\s]+([\d.]+)",       # 소수 형식
            r"Final.*?Acc[:\s]+([\d.]+)",    # Final validation 형식
        ]
        for pat in patterns:
            m = re.search(pat, output, re.IGNORECASE)
            if m:
                val = float(m.group(1))
                return val / 100 if val > 1.0 else val  # % → 소수 자동 변환
        return 0.0

    def get_status(self) -> dict:
        """현재 AutoTrainer 상태 반환"""
        return {
            "last_retrain":   self._last_retrain.isoformat() if self._last_retrain else "없음",
            "last_val_acc":   f"{self._last_val_acc*100:.2f}%",
            "retrain_count":  self._retrain_count,
            "is_training":    self._is_training,
            "next_retrain_in": (
                f"{self.RETRAIN_DAYS - (datetime.now() - self._last_retrain).days}일"
                if self._last_retrain else "다음 재시작 시"
            ),
        }