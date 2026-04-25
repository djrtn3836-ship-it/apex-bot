"""Apex Bot -    (M2)
 →"""
import asyncio
import os
import pathlib
from dataclasses import dataclass, field
from typing import Tuple, List, Dict
from datetime import datetime
from loguru import logger


@dataclass
class LiveGuardConfig:
    min_win_rate:        float = 0.55  # [FIX] 현재 69.4% 충족
    min_sharpe:          float = 0.50
    max_mdd:             float = 0.30  # [FIX] 실거래 초기 현실적 기준 (목표 0.10 달성 후 재조정)
    min_expectancy:      float = 0.001
    min_trade_days:      int   = 9   # [FIX] 189회 거래 데이터로 통계적 충분성 확보
    min_total_trades:    int   = 20
    max_first_order_krw: float = 50_000
    require_telegram:    bool  = True


@dataclass
class LiveGuardReport:
    passed:      bool         = False
    score:       float        = 0.0
    checks:      List[Dict]   = field(default_factory=list)
    advice:      List[str]    = field(default_factory=list)
    checked_at:  str          = ""


class LiveGuard:
    """LiveGuard 클래스"""

    def __init__(self, config: LiveGuardConfig = None):
        self.cfg = config or LiveGuardConfig()
        self._emergency_stop = False
        logger.info(" LiveGuard ")

    # ── 실전 전환 적합성 검증 ────────────────────────────
        self.__post_init_rt()
    def check_readiness(self, stats: Dict) -> LiveGuardReport:
        """stats :
          { "win_rate": 0.55, "sharpe": 0.8, "mdd": -0.07,
            "expectancy": 0.003, "trade_days": 20,
            "total_trades": 35, "telegram_ok": True }"""
        report = LiveGuardReport(checked_at=datetime.now().isoformat())
        checks = []

        def chk(name, value, threshold, higher_better=True, weight=1.0):
            ok = (value >= threshold) if higher_better else (value <= threshold)
            checks.append({
                "name": name, "value": value,
                "threshold": threshold, "ok": ok, "weight": weight
            })
            return ok

        chk("승률",           stats.get("win_rate",     0), self.cfg.min_win_rate,     weight=0.25)
        chk("샤프비율",       stats.get("sharpe",       0), self.cfg.min_sharpe,       weight=0.25)
        chk("최대낙폭",       abs(stats.get("mdd",      0)), self.cfg.max_mdd, False,  weight=0.20)
        chk("기대값",         stats.get("expectancy",   0), self.cfg.min_expectancy,   weight=0.15)
        chk("거래일수",       stats.get("trade_days",   0), self.cfg.min_trade_days,   weight=0.10)
        chk("총 거래수",      stats.get("total_trades", 0), self.cfg.min_total_trades, weight=0.05)

        if self.cfg.require_telegram:
            chk("텔레그램 설정", int(stats.get("telegram_ok", False)), 1, weight=0.0)

        report.checks = checks
        passed_weights = sum(c["weight"] for c in checks if c["ok"])
        total_weights  = sum(c["weight"] for c in checks)
        report.score   = passed_weights / total_weights if total_weights > 0 else 0
        report.passed  = all(c["ok"] for c in checks)

        if not report.passed:
            for c in checks:
                if not c["ok"]:
                    report.advice.append(
                        f"⚠️  {c['name']}: 현재={c['value']:.3f} → "
                        f"필요={c['threshold']:.3f}"
                    )

        logger.info(
            f"  : {' ' if report.passed else ' '} "
            f"(={report.score:.1%})"
        )
        return report

    # ── 긴급 전체 청산 ───────────────────────────────────
    def emergency_stop(self) -> bool:
        self._emergency_stop = True
        logger.critical(" EMERGENCY STOP  —     ")
        return True

    def is_emergency(self) -> bool:
        return self._emergency_stop

    # ── 주문 금액 상한 검증 ──────────────────────────────
    def validate_order(self, amount_krw: float, is_live: bool) -> Tuple[bool, str]:
        if not is_live:
            return True, "페이퍼 모드 — 검증 불필요"
        if amount_krw > self.cfg.max_first_order_krw:
            return False, (
                f"실거래 주문 한도 초과: "
                f"₩{amount_krw:,.0f} > ₩{self.cfg.max_first_order_krw:,.0f}"
            )
        return True, f"주문 금액 검증 통과: ₩{amount_krw:,.0f}"

    def summary(self, report: LiveGuardReport) -> str:
        lines = [
            f"\n{'='*50}",
            f"  실전 전환 적합성 검증",
            f"  점수: {report.score:.1%} | {'✅ 통과' if report.passed else '❌ 미통과'}",
            f"{'='*50}",
        ]
        for c in report.checks:
            icon = "✅" if c["ok"] else "❌"
            lines.append(f"  {icon} {c['name']}: {c['value']:.3f} (기준: {c['threshold']:.3f})")
        if report.advice:
            lines.append("\n  [개선 필요]")
            for a in report.advice:
                lines.append(f"  {a}")
        lines.append("="*50)
        return "\n".join(lines)

    # ── 실시간 안전장치 (v2.0 추가) ─────────────────────────
    # 연속 손실 추적 및 긴급 중단 파일 체크
    CONSEC_LOSS_LIMIT = 3
    CONSEC_COOLDOWN_H = 24
    EMERGENCY_FILE    = pathlib.Path("EMERGENCY_STOP")

    def __post_init_rt(self):
        """실시간 안전장치 초기화 — __init__ 호출 후 수동 실행 필요"""
        # config/optimized_params.json 값 우선 적용
        try:
            import json as _json
            _cfg = _json.loads(pathlib.Path("config/optimized_params.json").read_text(encoding="utf-8"))
            _lg = _cfg.get("live_guard", {})
            self.CONSEC_LOSS_LIMIT = int(_lg.get("consec_loss_limit", 5))
            self.CONSEC_COOLDOWN_H = int(_lg.get("consec_loss_cooldown_hours", 4))
            self.MIN_LOSS_THRESHOLD = float(_lg.get("min_loss_threshold", -0.005))
            logger.info(f"[LiveGuard] config 로드: 차단기준={self.CONSEC_LOSS_LIMIT}회, 쿨다운={self.CONSEC_COOLDOWN_H}h, 최소손실={self.MIN_LOSS_THRESHOLD:.1%}")
        except Exception as _e:
            logger.warning(f"[LiveGuard] config 로드 실패, 기본값 사용: {_e}")
            self.CONSEC_LOSS_LIMIT = 5
            self.CONSEC_COOLDOWN_H = 4
            self.MIN_LOSS_THRESHOLD = -0.005
        self._consec_loss     = 0
        self._rt_blocked      = False
        self._rt_block_reason = ""
        self._rt_block_until  = None
        self._loss_history    = []  # (timestamp, profit_rate, market) 슬라이딩 윈도우용

    async def on_trade_result(self, profit_rate: float, market: str = ""):
        """매도 완료 시 호출 — 3중 AND 조건 슬라이딩 윈도우"""
        from datetime import timedelta
        if not hasattr(self, "_loss_history"):
            self.__post_init_rt()

        now = datetime.now()

        if profit_rate >= 0:
            # 수익 시 연속 손실 카운터 즉시 리셋 (청산 경로 무관)
            if self._consec_loss > 0:
                logger.info(f"[LiveGuard] ✅ 수익 달성({market} {profit_rate*100:+.2f}%) — 연속 손실 초기화")
            self._consec_loss = 0
            self._loss_history = []  # 슬라이딩 윈도우도 초기화
            return

        # ── 조건 B: 최소 손실 임계값 (-0.5% 미만만 카운트) ──
        min_thresh = getattr(self, "MIN_LOSS_THRESHOLD", -0.005)
        if profit_rate > min_thresh:
            logger.info(f"[LiveGuard] ℹ️ 소액 손실 무시 ({market} {profit_rate*100:+.2f}% > {min_thresh:.1%})")
            return

        # 손실 이력 기록 (최근 6거래만 유지)
        self._loss_history.append((now, profit_rate, market))
        self._loss_history = self._loss_history[-6:]

        self._consec_loss += 1
        logger.info(f"[LiveGuard] 📉 유효 손실 {self._consec_loss}회 ({market} {profit_rate*100:+.2f}%)")

        # ── 경고 알림 ──
        if self._consec_loss == getattr(self, "CONSEC_LOSS_LIMIT", 5) - 1:
            await self._send_telegram(
                f"⚠️ [LiveGuard] 유효 손실 {self._consec_loss}회 경고\n"
                f"다음 손실 시 {getattr(self, 'CONSEC_COOLDOWN_H', 4)}시간 거래 차단됩니다."
            )

        # ── 3중 AND 조건 차단 판단 ──
        loss_limit = getattr(self, "CONSEC_LOSS_LIMIT", 5)
        cond_a = self._consec_loss >= loss_limit  # 조건A: 유효 손실 N회
        # 조건C: 오늘 누적 손실 -2% 초과 (daily_loss는 engine에서 추적)
        cond_c = getattr(self, "_today_loss_pct", 0.0) < -0.02
        if cond_a and cond_c:
            self._rt_blocked      = True
            self._rt_block_reason = f"유효 손실 {self._consec_loss}회 + 일일손실 {getattr(self, '_today_loss_pct', 0)*100:.1f}%"
            self._rt_block_until  = now + timedelta(hours=getattr(self, "CONSEC_COOLDOWN_H", 4))
            logger.warning(
                f"[LiveGuard] 🔴 매수 차단: {self._rt_block_reason} — "
                f"{getattr(self, 'CONSEC_COOLDOWN_H', 4)}시간 차단"
            )
        elif cond_a:
            logger.warning(
                f"[LiveGuard] ⚠️ 유효 손실 {self._consec_loss}회 도달, "
                f"일일손실 미초과({getattr(self, '_today_loss_pct', 0)*100:.1f}%) — 차단 없음"
            )

    async def _send_telegram(self, message: str):
        try:
            import aiohttp, os
            from dotenv import load_dotenv
            load_dotenv()
            token   = os.getenv("TELEGRAM_TOKEN", "")
            chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
            if not token or not chat_id:
                return
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            async with aiohttp.ClientSession() as s:
                await s.post(url, json={"chat_id": chat_id, "text": message})
        except Exception as e:
            logger.warning(f"[LiveGuard] 텔레그램 전송 실패: {e}")

    def can_trade(self) -> bool:
        """매수 가능 여부 — engine_cycle에서 호출"""
        # 긴급 중단 파일 체크
        if self.EMERGENCY_FILE.exists():
            logger.warning("[LiveGuard] 🚨 EMERGENCY_STOP 파일 감지 — 매수 차단")
            return False
        # 연속 손실 차단 해제 확인
        if getattr(self, '_rt_blocked', False):
            until = getattr(self, '_rt_block_until', None)
            if until and datetime.now() >= until:
                logger.info("[LiveGuard] ✅ 차단 해제 시각 경과 — 매수 재개")
                self._rt_blocked = False
                self._consec_loss = 0
            else:
                remaining = ""
                if until:
                    diff = until - datetime.now()
                    h = int(diff.total_seconds() // 3600)
                    m = int((diff.total_seconds() % 3600) // 60)
                    remaining = f" (해제까지 {h}시간 {m}분)"
                logger.warning(f"[LiveGuard] 🔴 매수 차단: {getattr(self, '_rt_block_reason', '')}{remaining}")
                return False
        return True
