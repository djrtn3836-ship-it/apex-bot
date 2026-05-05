"""
risk/risk_manager.py  -  Phase 8 개선  +  BUG-REAL-1-C 수정
  - GlobalRegime 연동 (BEAR/BEAR_WATCH 시 리스크 축소)
  - 4단계 서킷브레이커 (L1 경고 / L2 드로다운 / L3 급락 / L4 연속손실)
  - 일일 손실 한도 강화
  - [BUG-REAL-1-C] record_trade_result: profit_rate 저장 추가
  - [BUG-REAL-1-C] get_kelly_params: 실제 pnl 기반 동적 avg_win/avg_loss
  - [BUG-REAL-1-C] _calc_recent_win_rate: dict/bool 두 형식 호환
"""
from __future__ import annotations
import time
import asyncio
from typing import Tuple, Dict, Optional
from utils.logger import logger

try:
    from config.settings import get_settings
    _settings = get_settings()
except Exception:
    _settings = None


class RiskManager:
    def __init__(self):
        self.settings    = _settings
        self.risk_cfg    = getattr(_settings, "risk", None) if _settings else None
        self._paused_until   = 0.0
        self._pause_reason   = ""
        self._pause_level    = 0
        self._consecutive_losses = 0
        self._trade_results  = []   # {"win": bool, "pnl": float} 형식
        self._daily_loss     = 0.0
        self._daily_reset_ts = time.time()

    # ── 매수 허용 여부 ───────────────────────────────────────────────
    async def can_open_position(
        self,
        market: str,
        available_capital: float,
        current_positions: int,
        global_regime=None,
    ) -> Tuple[bool, str]:
        if self._is_paused():
            remaining = int(self._paused_until - time.time())
            return False, f"서킷브레이커 활성 ({remaining}초 남음): {self._pause_reason}"

        max_pos = getattr(getattr(self.settings, "trading", None), "max_positions", 10) if self.settings else 10
        if current_positions >= max_pos:
            return False, f"최대 포지션 도달 ({current_positions}/{max_pos})"

        min_size = getattr(self.risk_cfg, "min_position_size", 5000) if self.risk_cfg else 5000
        if available_capital < min_size:
            return False, f"가용 자본 부족 (\u20a9{available_capital:,.0f})"

        # GlobalRegime 연동: BEAR 시 추가 차단
        if global_regime is not None:
            regime_val = getattr(global_regime, "value", str(global_regime))
            if regime_val == "BEAR":
                return False, "BEAR 레짐 매수 차단"
            if regime_val == "BEAR_WATCH" and current_positions >= max(1, max_pos // 2):
                return False, f"BEAR_WATCH 레짐 포지션 제한 ({current_positions}/{max_pos//2})"

        return True, "OK"

    # ── 서킷브레이커 4단계 ───────────────────────────────────────────
    async def check_circuit_breaker(
        self,
        current_drawdown: float,
        total_value: float,
        global_regime=None,
    ) -> bool:
        dd_limit = getattr(self.risk_cfg, "total_drawdown_limit", 0.15) if self.risk_cfg else 0.15
        cl_limit = getattr(self.risk_cfg, "consecutive_loss_limit", 5) if self.risk_cfg else 5

        # L1: 드로다운 5% 경고 (1시간 매수 중단)
        if current_drawdown >= dd_limit * 0.33 * 100:
            if self._pause_level < 1:
                await self._trigger_circuit_breaker(1, 3600,
                    f"드로다운 경고 {current_drawdown:.1f}%")
                return True

        # L2: 드로다운 10% (48시간 중단)
        if current_drawdown >= dd_limit * 0.67 * 100:
            await self._trigger_circuit_breaker(2, 172800,
                f"드로다운 {current_drawdown:.1f}% 초과")
            return True

        # L3: 드로다운 한도 초과 (72시간 중단)
        if current_drawdown >= dd_limit * 100:
            await self._trigger_circuit_breaker(3, 259200,
                f"드로다운 한도 {current_drawdown:.1f}% 초과")
            return True

        # L4: 연속 손실 (24시간 중단)
        if self._consecutive_losses >= cl_limit:
            await self._trigger_circuit_breaker(4, 86400,
                f"연속 손실 {self._consecutive_losses}회")
            return True

        # GlobalRegime BEAR 시 L1 자동 활성
        if global_regime is not None:
            regime_val = getattr(global_regime, "value", str(global_regime))
            if regime_val == "BEAR" and not self._is_paused():
                await self._trigger_circuit_breaker(1, 3600, "BEAR 레짐 자동 방어")
                return True

        return False

    async def check_daily_loss_limit(self, daily_pnl: float) -> bool:
        # 일일 리셋 (자정 기준)
        if time.time() - self._daily_reset_ts > 86400:
            self._daily_loss = 0.0
            self._daily_reset_ts = time.time()
        self._daily_loss = min(self._daily_loss, daily_pnl)
        limit = getattr(self.risk_cfg, "daily_loss_limit", 0.03) if self.risk_cfg else 0.03
        if self._daily_loss <= -limit:
            await self._trigger_circuit_breaker(2, 86400,
                f"일일 손실 한도 {self._daily_loss*100:.1f}% 초과")
            return True
        return False

    # ── [BUG-REAL-1-C 수정] record_trade_result ─────────────────────
    def record_trade_result(self, is_win: bool, profit_rate: float = 0.0):
        """거래 결과 기록 — profit_rate(소수, 예: 0.032 = +3.2%)를 함께 저장"""
        self._trade_results.append({"win": is_win, "pnl": float(profit_rate)})
        if len(self._trade_results) > 100:
            self._trade_results.pop(0)
        if is_win:
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1

    # ── [BUG-REAL-1-C 수정] get_kelly_params ────────────────────────
    # [REFACTOR] get_kelly_params -> 수신 전담
    # Kelly 계산은 position_sizer.py DB 기반으로 단일화.
    # 이 메서드는 연속손실 추적 상태만 반환 (계산 로직 제거).
    def get_kelly_params(self) -> Dict:
        """연속손실/승률 상태 반환 (Kelly 계산은 position_sizer.py 전담)"""
        return {
            "win_rate":    self._calc_recent_win_rate(),
            "consec_loss": self._consecutive_losses,
            "trade_count": len(self._trade_results),
        }

    # ── [BUG-REAL-1-C 수정] _calc_recent_win_rate ───────────────────
    def _calc_recent_win_rate(self) -> float:
        """최근 20개 거래 승률 — dict / bool 두 형식 모두 지원"""
        if len(self._trade_results) < 5:
            return 0.55
        recent = self._trade_results[-20:]
        try:
            if isinstance(recent[0], dict):
                wins = sum(1 for r in recent if r.get("win"))
            else:
                wins = sum(1 for r in recent if r)
            return wins / len(recent)
        except Exception:
            return 0.55

    async def _trigger_circuit_breaker(self, level: int, duration: float, reason: str):
        if self._pause_level >= level and self._is_paused():
            return
        self._paused_until = time.time() + duration
        self._pause_reason = reason
        self._pause_level  = level
        hours = duration / 3600
        logger.warning(f"[CircuitBreaker L{level}] {reason} \u2192 {hours:.0f}시간 중단")

    def _is_paused(self) -> bool:
        if self._paused_until > time.time():
            return True
        if self._pause_level > 0:
            self._pause_level = 0
        return False

    def force_resume(self):
        self._paused_until = 0.0
        self._pause_level  = 0
        logger.info("[RiskManager] 강제 재개")

    def reset_daily(self):
        self._daily_loss     = 0.0
        self._daily_reset_ts = time.time()
        self._consecutive_losses = 0

    def get_status(self) -> Dict:
        return {
            "paused":       self._is_paused(),
            "pause_level":  self._pause_level,
            "pause_reason": self._pause_reason,
            "resume_in":    max(0, int(self._paused_until - time.time())),
            "consec_loss":  self._consecutive_losses,
            "win_rate":     self._calc_recent_win_rate(),
            "daily_loss":   self._daily_loss,
        }
