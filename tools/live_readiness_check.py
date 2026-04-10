"""APEX BOT -    
        .

:
    python tools/live_readiness_check.py
    python tools/live_readiness_check.py --days 30"""
import sys
import os
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger


# ── 실매매 전환 기준 (보수적 설정) ───────────────────────────────
CRITERIA = {
    # 필수 기간 (충분한 샘플 확보)
    "min_trading_days": 14,          # 최소 2주 이상 운영
    "min_trades": 30,                # 최소 30회 이상 거래

    # 수익성 기준
    "min_total_pnl_pct": 3.0,        # 2주 기준 총 수익 +3% 이상
    "min_win_rate": 52.0,            # 승률 52% 이상 (랜덤 50% 초과)

    # 리스크 기준
    "max_drawdown_pct": 8.0,         # 최대 낙폭 8% 미만
    "max_daily_loss_pct": 3.0,       # 일일 최대 손실 3% 미만

    # 안정성 기준
    "min_sharpe": 1.0,               # 샤프 비율 1.0 이상
    "min_profit_factor": 1.3,        # 수익 팩터 1.3 이상
    "max_consecutive_losses": 4,     # 최대 연속 손실 4회 미만

    # 시장 커버리지 (다양한 시장 상황 경험 여부)
    "min_btc_dump_events": 1,        # BTC 3% 이상 급락 이벤트 1회 이상 경험
}


class LiveReadinessChecker:
    """."""

    def __init__(self, days: int = 14):
        self.days = days
        self.results: Dict[str, Any] = {}
        self.passed: Dict[str, bool] = {}
        self.score = 0
        self.max_score = 0

    def _load_paper_metrics(self) -> Dict[str, Any]:
        """docstring"""
        try:
            import config.settings as settings_module
            from config.settings import Settings
            settings_module._settings = Settings(mode="paper")

            from monitoring.paper_report import generate_paper_report
            hours = self.days * 24
            result = generate_paper_report(hours=hours, output_dir="reports/paper")
            return result.get("metrics", {})
        except Exception as e:
            logger.error(f"  : {e}")
            return {}

    def _load_trade_history(self):
        """DB"""
        try:
            import sqlite3
            db_path = "database/apex_bot.db"
            if not Path(db_path).exists():
                return []

            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            since = datetime.now() - timedelta(days=self.days)
            cursor.execute("""
                SELECT * FROM trades
                WHERE created_at >= ?
                ORDER BY created_at DESC
            """, (since.isoformat(),))
            trades = cursor.fetchall()
            conn.close()
            return trades
        except Exception:
            return []

    def check(self) -> Dict[str, Any]:
        """docstring"""
        print("\n" + "="*65)
        print("   APEX BOT -    ")
        print(f"   :  {self.days}")
        print("="*65)

        metrics = self._load_paper_metrics()
        trades = self._load_trade_history()

        if not metrics:
            print("\n    ")
            print("    2  paper    ")
            return {"ready": False, "reason": "데이터 부족"}

        # ── 지표 추출 ─────────────────────────────────────────────
        total_pnl      = metrics.get("total_pnl_pct", 0)
        win_rate       = metrics.get("win_rate", 0)
        max_dd         = metrics.get("max_drawdown_pct", 0)
        sharpe         = metrics.get("sharpe_ratio", 0)
        total_trades   = metrics.get("total_trades", 0)
        profit_factor  = metrics.get("profit_factor", 0)
        consec_losses  = metrics.get("max_consecutive_losses", 0)
        trading_days   = metrics.get("trading_days", self.days)

        # ── 각 기준 평가 ──────────────────────────────────────────
        checks = [
            # (기준명, 실제값, 기준값, 단위, 통과조건, 점수)
            ("운영 기간",      trading_days,  CRITERIA["min_trading_days"],  "일",  trading_days >= CRITERIA["min_trading_days"],  10),
            ("거래 횟수",      total_trades,  CRITERIA["min_trades"],        "회",  total_trades >= CRITERIA["min_trades"],        10),
            ("총 수익률",      total_pnl,     CRITERIA["min_total_pnl_pct"], "%",   total_pnl >= CRITERIA["min_total_pnl_pct"],    20),
            ("승률",           win_rate,      CRITERIA["min_win_rate"],      "%",   win_rate >= CRITERIA["min_win_rate"],           15),
            ("최대 낙폭",      max_dd,        CRITERIA["max_drawdown_pct"],  "%",   max_dd <= CRITERIA["max_drawdown_pct"],         20),
            ("샤프 비율",      sharpe,        CRITERIA["min_sharpe"],        "",    sharpe >= CRITERIA["min_sharpe"],               15),
            ("수익 팩터",      profit_factor, CRITERIA["min_profit_factor"], "x",   profit_factor >= CRITERIA["min_profit_factor"], 10),
            ("연속 손실",      consec_losses, CRITERIA["max_consecutive_losses"], "회", consec_losses <= CRITERIA["max_consecutive_losses"], 10),
        ]

        print("\n  [ ]")
        print(f"  {'':<16} {'':>10}  {'':>10}   {''}")
        print("  " + "-"*55)

        total_score = 0
        max_possible = 0
        critical_fails = []

        for name, actual, threshold, unit, passed, weight in checks:
            max_possible += weight
            icon = "✅" if passed else "❌"

            if name in ["총 수익률", "최대 낙폭"]:
                # 핵심 기준 (미통과 시 실매매 불가)
                critical = not passed
            else:
                critical = False

            if passed:
                total_score += weight
                status = "통과"
            else:
                status = "미달"
                if critical:
                    critical_fails.append(name)

            # 값 포맷
            if isinstance(actual, float):
                val_str = f"{actual:+.2f}{unit}" if unit == "%" else f"{actual:.2f}{unit}"
            else:
                val_str = f"{actual}{unit}"
            thr_str = f"{threshold}{unit}"

            print(f"  {icon} {name:<14} {val_str:>10}  {thr_str:>10}   {status}")

        # ── 종합 점수 ─────────────────────────────────────────────
        score_pct = (total_score / max_possible) * 100 if max_possible else 0
        all_passed = len(critical_fails) == 0 and score_pct >= 70

        print("\n" + "="*65)
        print(f"\n    : {total_score}/{max_possible} ({score_pct:.0f}%)")

        # ── 판정 ──────────────────────────────────────────────────
        if all_passed and score_pct >= 85:
            verdict = "🟢 실매매 전환 가능"
            color_guide = "✅ 모든 기준 통과 — 소액부터 시작 권장"
        elif all_passed and score_pct >= 70:
            verdict = "🟡 조건부 전환 가능"
            color_guide = "⚠️  핵심 기준은 통과, 1~2주 추가 관찰 권장"
        elif critical_fails:
            verdict = "🔴 실매매 전환 불가"
            color_guide = f"❌ 핵심 기준 미달: {', '.join(critical_fails)}"
        else:
            verdict = "🟠 추가 검증 필요"
            color_guide = "⚠️  점수 부족 — 계속 페이퍼 모드 운영"

        print(f"\n   : {verdict}")
        print(f"  {color_guide}")

        # ── 실매매 전환 가이드 ────────────────────────────────────
        if all_passed and score_pct >= 70:
            print("\n" + "="*65)
            print("     ")
            print("="*65)
            checklist = [
                ("업비트 API 키 발급 및 입력",         ".env → UPBIT_ACCESS_KEY / SECRET_KEY"),
                ("초기 투자금 결정",                    "권장: 총 자산의 5~10% 이하 (손실 감당 가능 금액)"),
                ("리스크 설정 재확인",                  "config/settings.py → max_risk_per_trade=0.02 (2%)"),
                ("텔레그램 알림 설정",                  ".env → TELEGRAM_TOKEN / CHAT_ID"),
                ("서킷브레이커 확인",                   "daily_loss_limit=0.05 (5%), total_drawdown=0.10 (10%)"),
                ("실매매 시작",                         "python main.py --mode live"),
            ]
            for i, (task, desc) in enumerate(checklist, 1):
                print(f"  {i}.  {task}")
                print(f"        {desc}")
            print()

        print("="*65)

        return {
            "ready": all_passed and score_pct >= 70,
            "score_pct": score_pct,
            "verdict": verdict,
            "critical_fails": critical_fails,
            "metrics": metrics,
        }


def main():
    parser = argparse.ArgumentParser(description="실매매 전환 준비도 체크")
    parser.add_argument("--days", type=int, default=14, help="분석 기간 (일)")
    args = parser.parse_args()

    checker = LiveReadinessChecker(days=args.days)
    result = checker.check()
    sys.exit(0 if result["ready"] else 1)


if __name__ == "__main__":
    main()
