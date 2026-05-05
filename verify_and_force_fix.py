# verify_and_force_fix.py
# risk/risk_manager.py 의 BUG-REAL-1-C 실제 적용 여부 확인 후 미적용이면 강제 교체

import os, ast, shutil
from datetime import datetime

_TS  = datetime.now().strftime("%Y%m%d_%H%M%S")
_BAK = os.path.join("archive", f"verify_force_{_TS}")
os.makedirs(_BAK, exist_ok=True)
print(f"\n📁 백업 경로: {_BAK}\n")

RISK_PATH = os.path.join("risk", "risk_manager.py")

# ══════════════════════════════════════════════════════════════
# 1. 현재 로컬 파일 상태 진단
# ══════════════════════════════════════════════════════════════
if not os.path.isfile(RISK_PATH):
    print(f"❌ 파일 없음: {RISK_PATH}")
    exit(1)

with open(RISK_PATH, "r", encoding="utf-8") as f:
    current = f.read()

checks = {
    "record_profit_rate" : "profit_rate"          in current,
    "kelly_wins_pnl"     : "wins_pnl"             in current,
    "kelly_dynamic"      : "동적 Kelly 계산"       in current,
    "winrate_dict"       : 'r.get("win")'          in current,
}

print("=" * 60)
print("🔍 로컬 파일 수정 적용 여부 확인")
print("=" * 60)
for k, v in checks.items():
    icon = "✅" if v else "❌ 미적용"
    print(f"  {icon}  {k}")

all_applied = all(checks.values())

if all_applied:
    print("\n✅ 모든 수정이 이미 로컬 파일에 적용돼 있습니다.")
    print("   → python main.py --mode paper 로 바로 검증 진행하세요.\n")
    exit(0)

# ══════════════════════════════════════════════════════════════
# 2. 미적용 항목 있음 → 파일 전체를 완성본으로 강제 교체
# ══════════════════════════════════════════════════════════════
print("\n⚠️  미적용 항목 발견 → 파일 전체를 완성본으로 교체합니다...")

# 백업
shutil.copy2(RISK_PATH, os.path.join(_BAK, "risk_manager.py.bak"))
print(f"   백업 완료: {_BAK}/risk_manager.py.bak")

FIXED_CONTENT = '''\
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
            return False, f"가용 자본 부족 (\\u20a9{available_capital:,.0f})"

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
        limit = getattr(self.risk_cfg, "daily_loss_limit", 0.05) if self.risk_cfg else 0.05
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
    def get_kelly_params(self) -> Dict:
        """동적 Kelly 계산 — 실제 거래 pnl 기반 avg_win / avg_loss 산출"""
        wr = self._calc_recent_win_rate()

        # 기본값 (거래 이력 부족 시 보수적 고정값)
        avg_win  = 0.020   # 기본 2%
        avg_loss = 0.015   # 기본 1.5%

        # 실제 pnl 데이터가 있으면 동적 계산
        if len(self._trade_results) >= 10:
            try:
                if isinstance(self._trade_results[0], dict):
                    wins_pnl = [abs(r["pnl"]) for r in self._trade_results
                                if r.get("win") and r.get("pnl", 0) > 0]
                    loss_pnl = [abs(r["pnl"]) for r in self._trade_results
                                if not r.get("win") and r.get("pnl", 0) < 0]
                else:
                    # 이전 버전 호환 (bool 리스트)
                    wins_pnl = []
                    loss_pnl = []

                if wins_pnl:
                    avg_win  = max(0.005, min(sum(wins_pnl) / len(wins_pnl), 0.20))
                if loss_pnl:
                    avg_loss = max(0.005, min(sum(loss_pnl) / len(loss_pnl), 0.20))
            except Exception:
                pass  # 파싱 실패 시 기본값 유지

        kelly = (wr * avg_win - (1 - wr) * avg_loss) / avg_win if avg_win > 0 else 0.10
        kelly = max(0.05, min(kelly, 0.20))
        return {"win_rate": wr, "avg_win": avg_win, "avg_loss": avg_loss, "kelly": kelly}

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
        logger.warning(f"[CircuitBreaker L{level}] {reason} \\u2192 {hours:.0f}시간 중단")

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
'''

# 문법 검사 후 저장
try:
    ast.parse(FIXED_CONTENT)
except SyntaxError as e:
    print(f"❌ 내장 코드 문법 오류 (스크립트 버그): {e}")
    exit(1)

with open(RISK_PATH, "w", encoding="utf-8") as f:
    f.write(FIXED_CONTENT)

# ══════════════════════════════════════════════════════════════
# 3. 재검증
# ══════════════════════════════════════════════════════════════
with open(RISK_PATH, "r", encoding="utf-8") as f:
    updated = f.read()

checks2 = {
    "record_profit_rate" : "profit_rate"    in updated,
    "kelly_wins_pnl"     : "wins_pnl"       in updated,
    "kelly_dynamic"      : "동적 Kelly 계산" in updated,
    "winrate_dict"       : 'r.get("win")'   in updated,
}

print("\n" + "=" * 60)
print("🔍 교체 후 재검증")
print("=" * 60)
all_ok = True
for k, v in checks2.items():
    icon = "✅" if v else "❌"
    print(f"  {icon}  {k}")
    if not v:
        all_ok = False

print()
if all_ok:
    print("✅ BUG-REAL-1-C 수정 완료 — risk/risk_manager.py")
    print()
    print("📋 다음 단계:")
    print("   1. record_trade_result 호출부 확인:")
    print("      grep -rn 'record_trade_result' . --include='*.py'")
    print("      → 호출 시 profit_rate 인자를 함께 전달해야 동적 Kelly가 동작합니다.")
    print("      예) self.risk_manager.record_trade_result(is_win=True, profit_rate=0.032)")
    print()
    print("   2. 페이퍼 트레이딩 실행:")
    print("      python main.py --mode paper")
else:
    print("❌ 일부 항목 미적용 — 스크립트 오류, 수동 확인 필요")
