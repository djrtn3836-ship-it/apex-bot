"""Apex Bot -    (M2)
 →"""
import os
from dataclasses import dataclass, field
from typing import Tuple, List, Dict
from datetime import datetime
from loguru import logger


@dataclass
class LiveGuardConfig:
    min_win_rate:        float = 0.50
    min_sharpe:          float = 0.50
    max_mdd:             float = 0.10
    min_expectancy:      float = 0.001
    min_trade_days:      int   = 14
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
