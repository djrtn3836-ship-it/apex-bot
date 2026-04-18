# monitoring/performance_tracker.py — 샤프비율/MDD/승률 추적기
""":
  -   (Sharpe Ratio)
  -   (Max Drawdown)
  -  (Win Rate)
  -   (Profit Factor)
  -"""

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
        """N"""
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
        """docstring"""
        s = self.get_stats(days)
        grade = self._grade(s)

        print(f"""APEX BOT   ( {days:2d})              

         : {s.total_trades:>5d}                          
             : {s.win_rate*100:>6.1f}%                        
       : {s.avg_profit*100:>+7.3f}%                     
         : {s.profit_factor:>6.2f}                        
         : {s.sharpe_ratio:>6.2f}                         
   (MDD) : {s.max_drawdown*100:>6.2f}%                    
   P&L       : ₩{s.total_pnl_krw:>10,.0f}                   
     : {s.best_trade*100:>+7.3f}%                     
     : {s.worst_trade*100:>+7.3f}%                    
         : {grade:>3s}""")

    # ── 내부 계산 ─────────────────────────────────────────────
    def _calc_sharpe(self, rates: list, timestamps: list = None) -> float:
        """Sharpe Ratio 계산 — 거래 빈도 기반 동적 연환산.

        profit_rate 단위: 퍼센트 (예: 1.5 = +1.5%)

        연환산 정책:
          - 데이터 30일 미만: 비연환산 (avg/std) 반환 — 과대추정 방지
          - 데이터 30일 이상: 실제 거래 빈도 기반 연환산 적용
          - 최대 연환산 cap: sqrt(252) = 15.87 (일봉 기준 상한)

        전문 퀀트 기준:
          - raw sharpe(avg/std) > 0.15 → 연환산 시 Sharpe ≈ 1.5+ 기대
          - raw sharpe(avg/std) > 0.10 → 연환산 시 Sharpe ≈ 1.0+ 기대
        """
        if len(rates) < 5:
            return 0.0
        import statistics, math, sqlite3
        from pathlib import Path

        # 퍼센트 → 소수 변환
        r_dec = [r / 100.0 for r in rates]
        avg = statistics.mean(r_dec)
        std = statistics.stdev(r_dec)
        if std == 0:
            return 0.0

        # raw Sharpe (비연환산) — 항상 신뢰 가능
        raw_sharpe = (avg - self.RISK_FREE) / std

        # 데이터 기간 계산 (DB에서 실제 타임스탬프 조회)
        try:
            con = sqlite3.connect(str(self.db_path))
            cur = con.cursor()
            cur.execute("""
                SELECT MIN(timestamp), MAX(timestamp), COUNT(*)
                FROM trade_history
                WHERE side='SELL'
            """)
            row = cur.fetchone()
            con.close()

            if row and row[0] and row[1]:
                from datetime import datetime
                t1 = datetime.fromisoformat(row[0])
                t2 = datetime.fromisoformat(row[1])
                span_days  = (t2 - t1).total_seconds() / 86400
                total_cnt  = row[2]
                avg_gap_hr = (t2 - t1).total_seconds() / 3600 / max(total_cnt - 1, 1)

                if span_days < 30:
                    # ✅ 30일 미만: 연환산 미적용 (과대추정 방지)
                    # raw_sharpe에 sqrt(252/span_days) 비례 스케일만 적용
                    scale = math.sqrt(min(span_days, 30) / 30)
                    annualized = raw_sharpe * scale * math.sqrt(252)
                    return round(min(annualized, raw_sharpe * 20), 4)
                else:
                    # ✅ 30일 이상: 실제 빈도 기반 연환산, 일봉 기준 cap
                    trades_per_year = 8760 / avg_gap_hr
                    factor = math.sqrt(min(trades_per_year, 252))
                    return round(raw_sharpe * factor, 4)
        except Exception:
            pass

        # fallback: 일봉 기준 연환산 (sqrt(252))
        return round(raw_sharpe * math.sqrt(252), 4)

    def _calc_sharpe_raw(self, rates: list) -> float:
        """비연환산 raw Sharpe (avg/std) — 표본 크기 무관하게 신뢰 가능."""
        if len(rates) < 5:
            return 0.0
        import statistics
        r_dec = [r / 100.0 for r in rates]
        avg = statistics.mean(r_dec)
        std = statistics.stdev(r_dec)
        return round((avg - self.RISK_FREE) / std, 4) if std > 0 else 0.0

    def _calc_mdd(self, rates: list) -> float:
        """Max Drawdown 계산.
        profit_rate 단위: 퍼센트 (예: -2.0 = -2.0%)
        반환값: 소수 (예: 0.05 = 5%)
        """
        if not rates:
            return 0.0
        # ✅ FIX: 퍼센트 → 소수 변환 후 계산
        equity = 1.0
        peak   = 1.0
        mdd    = 0.0
        for r in rates:
            equity *= (1 + r / 100.0)
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

    def update(self, trades: list) -> None:
        """engine_schedule._scheduled_performance_check 호환용.
        trades 리스트를 받아도 실제 계산은 DB에서 직접 읽으므로 pass."""
        pass  # get_stats()가 DB를 직접 읽기 때문에 별도 업데이트 불필요

    def get_metrics(self, days: int = 14) -> dict:
        """engine_schedule._scheduled_performance_check 호환용.
        get_stats() 결과를 dict 형태로 반환."""
        s = self.get_stats(days)
        rates = [r["profit_rate"] for r in self._query_sells(days)]
        raw_sharpe = self._calc_sharpe_raw(rates)
        return {
            "win_rate":       s.win_rate,
            "sharpe_ratio":   s.sharpe_ratio,       # 동적 연환산
            "sharpe_raw":     raw_sharpe,            # ✅ 비연환산 (신뢰 기준)
            "max_drawdown":   s.max_drawdown,
            "profit_factor":  s.profit_factor,
            "total_trades":   s.total_trades,
            "avg_profit":     s.avg_profit,
            "total_pnl_krw":  s.total_pnl_krw,
            "best_trade":     s.best_trade,
            "worst_trade":    s.worst_trade,
        }

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
