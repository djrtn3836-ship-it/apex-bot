# monitoring/performance_tracker.py — 샤프비율/MDD/승률 추적기
"""
실시간 성과 지표:
  - 샤프 비율 (Sharpe Ratio)
  - 최대 낙폭 (Max Drawdown)
  - 승률 (Win Rate)
  - 수익 팩터 (Profit Factor)
  - 평균 보유 시간
"""

import sqlite3
import math
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from utils.logger import logger


@dataclass
class PerformanceStats:
    total_trades:   int
    win_rate:       float   # 0~1
    avg_profit:     float   # 소수 (0.01 = 1%)
    profit_factor:  float   # gross_profit / gross_loss
    sharpe_ratio:   float
    max_drawdown:   float   # 소수 (0.05 = 5%)
    total_pnl_krw:  float
    best_trade:     float
    worst_trade:    float


class PerformanceTracker:
    DB_PATH     = Path("database/apex_bot.db")
    RISK_FREE   = 0.0      # 무위험 수익률 (0% 가정)

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or self.DB_PATH

    def get_stats(self, days: int = 14) -> PerformanceStats:
        """
        최근 N일 성과 통계 계산
        """
        rows = self._query_sells(days)

        if not rows:
            return PerformanceStats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        rates = [r["profit_rate"] for r in rows]
        amts  = [r["amount_krw"] for r in rows]

        wins   = [r for r in rates if r > 0]
        losses = [r for r in rates if r <= 0]

        win_rate      = len(wins) / len(rates) if rates else 0
        avg_profit    = sum(rates) / len(rates) if rates else 0
        gross_profit  = sum(r * a for r, a in zip(rates, amts) if r > 0)
        gross_loss    = abs(sum(r * a for r, a in zip(rates, amts) if r <= 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        total_pnl_krw = sum(r * a for r, a in zip(rates, amts))

        sharpe_ratio  = self._calc_sharpe(rates)
        max_drawdown  = self._calc_mdd(rates)

        return PerformanceStats(
            total_trades  = len(rates),
            win_rate      = win_rate,
            avg_profit    = avg_profit,
            profit_factor = profit_factor,
            sharpe_ratio  = sharpe_ratio,
            max_drawdown  = max_drawdown,
            total_pnl_krw = total_pnl_krw,
            best_trade    = max(rates) if rates else 0,
            worst_trade   = min(rates) if rates else 0,
        )

    def print_report(self, days: int = 14):
        """성과 리포트 출력"""
        s = self.get_stats(days)
        grade = self._grade(s)

        print(f"""
╔══════════════════════════════════════════════════════╗
║     📊 APEX BOT 성과 리포트 (최근 {days:2d}일)              ║
╠══════════════════════════════════════════════════════╣
║  총 거래수      : {s.total_trades:>5d}건                          ║
║  승률           : {s.win_rate*100:>6.1f}%                        ║
║  평균 수익률    : {s.avg_profit*100:>+7.3f}%                     ║
║  수익 팩터      : {s.profit_factor:>6.2f}                        ║
║  샤프 비율      : {s.sharpe_ratio:>6.2f}                         ║
║  최대 낙폭(MDD) : {s.max_drawdown*100:>6.2f}%                    ║
║  누적 P&L       : ₩{s.total_pnl_krw:>10,.0f}                   ║
║  최고 수익 거래 : {s.best_trade*100:>+7.3f}%                     ║
║  최악 손실 거래 : {s.worst_trade*100:>+7.3f}%                    ║
║  종합 등급      : {grade:>3s}                                ║
╚══════════════════════════════════════════════════════╝
""")

    # ── 내부 계산 ─────────────────────────────────────────────
    def _calc_sharpe(self, rates: list) -> float:
        if len(rates) < 2:
            return 0.0
        import statistics
        avg = statistics.mean(rates)
        std = statistics.stdev(rates)
        if std == 0:
            return 0.0
        # 연환산 (60분봉 기준: 8760시간/년)
        return (avg - self.RISK_FREE) / std * math.sqrt(8760)

    def _calc_mdd(self, rates: list) -> float:
        if not rates:
            return 0.0
        equity = 1.0
        peak   = 1.0
        mdd    = 0.0
        for r in rates:
            equity *= (1 + r)
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            if dd > mdd:
                mdd = dd
        return mdd

    def _grade(self, s: PerformanceStats) -> str:
        if s.total_trades < 5:
            return "📊 데이터 부족"
        score = 0
        if s.win_rate      >= 0.55: score += 2
        elif s.win_rate    >= 0.45: score += 1
        if s.sharpe_ratio  >= 1.5:  score += 2
        elif s.sharpe_ratio >= 0.8: score += 1
        if s.profit_factor >= 2.0:  score += 2
        elif s.profit_factor >= 1.3: score += 1
        if s.max_drawdown  <= 0.05: score += 1

        if score >= 6: return "🏆 S (최우수)"
        if score >= 4: return "🥇 A (우수)"
        if score >= 2: return "🥈 B (양호)"
        return "🥉 C (개선 필요)"

    def _query_sells(self, days: int) -> list:
        if not self.db_path.exists():
            return []
        con = sqlite3.connect(str(self.db_path))
        cur = con.cursor()
        try:
            cur.execute(
                """
                SELECT profit_rate, amount_krw, timestamp
                FROM trade_history
                WHERE side='SELL'
                  AND timestamp >= datetime('now', ?)
                ORDER BY timestamp
                """,
                (f"-{days} days",),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            con.close()
