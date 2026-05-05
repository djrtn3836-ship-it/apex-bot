
# [G1_MTFGate] signals/mtf_gate.py
# MTF 필수 컨펌 게이트 — 1d/4h 방향이 1h와 불일치 시 진입 완전 차단
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional
import pandas as pd
import numpy as np
from loguru import logger

@dataclass
class MTFGateResult:
    allowed: bool           # 진입 허용 여부
    score: float            # -1.0(완전 역방향) ~ +1.0(완전 동방향)
    reason: str
    tf_directions: Dict[str, int]  # 타임프레임별 방향 (-1/0/+1)
    is_softfail: bool = False       # 데이터 부족 등 소프트 통과


class MTFGate:
    """
    멀티 타임프레임 필수 컨펌 게이트
    타임프레임 가중치: 1d=0.50, 4h=0.30, 1h=0.20

    진입 허용 조건:
      - weighted_score >= GATE_THRESHOLD_BULL (GlobalRegime=BULL: -0.10)
      - weighted_score >= GATE_THRESHOLD_DEFAULT (기타: 0.0)
      - 데이터 부족(softfail) 시 항상 허용
      - BEAR_REVERSAL 시 역방향 허용 (score >= -0.50)
    """

    TF_WEIGHTS = {"1d": 0.50, "4h": 0.30, "1h": 0.20}
    GATE_THRESHOLD_BULL    = -0.30   # BULL: [BSF-1] 역방향 허용 확대
    GATE_THRESHOLD_DEFAULT = 0.0     # 기타: 동방향 필수
    GATE_THRESHOLD_BEAR_REV = -0.50  # BEAR_REVERSAL: 역방향 대부분 허용
    MIN_CANDLES = 20                 # 방향 판단 최소 캔들 수

    def check(
        self,
        tf_data: Dict[str, pd.DataFrame],
        signal_direction: int,     # +1=BUY, -1=SELL
        global_regime: str = "UNKNOWN",
        is_bear_reversal: bool = False,
    ) -> MTFGateResult:
        """
        MTF 방향 일치 여부 판단

        Args:
            tf_data: {"1d": df, "4h": df, "1h": df} 형태
            signal_direction: 진입 방향 (+1 BUY / -1 SELL)
            global_regime: GlobalRegime 문자열
            is_bear_reversal: BEAR_REVERSAL 플래그
        """
        directions: Dict[str, int] = {}
        total_weight = 0.0
        weighted_dir = 0.0
        softfail_tfs = []

        for tf, weight in self.TF_WEIGHTS.items():
            df = tf_data.get(tf)
            if df is None or len(df) < self.MIN_CANDLES:
                softfail_tfs.append(tf)
                continue
            direction = self._calc_direction(df)
            directions[tf] = direction
            weighted_dir += direction * weight
            total_weight += weight

        # 중요 TF(1d, 4h) 모두 데이터 없음 → softfail 통과
        if total_weight < 0.30 or (not directions):
            logger.debug(
                f"[MTFGate] softfail (데이터부족: {softfail_tfs}) → 통과"
            )
            return MTFGateResult(
                allowed=True,
                score=0.0,
                reason=f"데이터 부족 ({softfail_tfs}) → softfail 통과",
                tf_directions=directions,
                is_softfail=True,
            )

        # 가중치 합산 (전체 TF가 없어도 있는 TF만으로 정규화)
        norm_score = weighted_dir / total_weight

        # signal_direction과 일치 방향으로 변환
        # norm_score: -1(완전 역) ~ +1(완전 동)
        # signal이 BUY(+1)이면 norm_score 그대로, SELL(-1)이면 부호 반전
        aligned_score = norm_score * signal_direction

        # 레짐별 임계값 결정
        _regime_upper = str(global_regime).upper()
        if is_bear_reversal:
            threshold = self.GATE_THRESHOLD_BEAR_REV
        elif _regime_upper in ("BULL", "RECOVERY"):
            threshold = self.GATE_THRESHOLD_BULL
        else:
            threshold = self.GATE_THRESHOLD_DEFAULT

        allowed = aligned_score >= threshold

        # 방향 문자열 생성
        _dir_str = " | ".join(
            f"{tf}={'▲' if d > 0 else ('▼' if d < 0 else '─')}"
            for tf, d in directions.items()
        )
        reason = (
            f"MTFGate score={aligned_score:.2f} thr={threshold:.2f} "
            f"[{_dir_str}] → {'허용' if allowed else '차단'}"
        )

        if not allowed:
            logger.info(f"[MTFGate] ❌ {reason}")
        else:
            logger.debug(f"[MTFGate] ✅ {reason}")

        return MTFGateResult(
            allowed=allowed,
            score=aligned_score,
            reason=reason,
            tf_directions=directions,
            is_softfail=False,
        )

    def _calc_direction(self, df: pd.DataFrame) -> int:
        """
        캔들 데이터로 방향 판단 (-1 / 0 / +1)
        복합 판단: EMA 기울기 + 최근 3캔들 방향 + RSI 위치
        """
        try:
            close = df["close"].values.astype(float)
            n = len(close)

            # ── EMA20 기울기 ────────────────────────────────
            _w = np.ones(20) / 20
            if n >= 20:
                ema20 = np.convolve(close, _w, mode="valid")
                ema_slope = (ema20[-1] - ema20[-5]) / (ema20[-5] + 1e-9) if len(ema20) >= 5 else 0
            else:
                ema_slope = (close[-1] - close[0]) / (close[0] + 1e-9)

            # ── 최근 3캔들 방향 ─────────────────────────────
            recent_dir = 1 if close[-1] > close[-4] else -1 if close[-1] < close[-4] else 0

            # ── RSI (간이) ──────────────────────────────────
            rsi_dir = 0
            if "rsi" in df.columns:
                rsi_val = float(df["rsi"].iloc[-1])
                rsi_dir = 1 if rsi_val > 55 else (-1 if rsi_val < 45 else 0)
            elif n >= 14:
                delta = np.diff(close[-15:])
                gain  = np.mean(delta[delta > 0]) if np.any(delta > 0) else 0
                loss  = np.mean(-delta[delta < 0]) if np.any(delta < 0) else 1e-9
                rs    = gain / (loss + 1e-9)
                rsi_val = 100 - 100 / (1 + rs)
                rsi_dir = 1 if rsi_val > 55 else (-1 if rsi_val < 45 else 0)

            # ── 종합 판단 ────────────────────────────────────
            # EMA 기울기 0.5% 이상이면 방향 확정
            if ema_slope > 0.005:
                ema_dir = 1
            elif ema_slope < -0.005:
                ema_dir = -1
            else:
                ema_dir = 0

            # 3가지 신호 합산 → -3~+3, 임계값 1 이상이면 방향 결정
            composite = ema_dir + recent_dir + rsi_dir
            if composite >= 1:
                return 1
            elif composite <= -1:
                return -1
            else:
                return 0

        except Exception as _e:
            logger.debug(f"[MTFGate] _calc_direction 오류: {_e}")
            return 0
