"""Apex Bot -      (M7-C)"""
import sqlite3
import pathlib
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Tuple
from loguru import logger


class LiveReadinessChecker:
    """LiveReadinessChecker 클래스"""

    def __init__(self, db_path: str = "database/apex_bot.db"):
        self.db_path = pathlib.Path(db_path)
        logger.info(" LiveReadinessChecker ")

    def check(self) -> Tuple[bool, float, Dict]:
        """: (,  0~1,  )"""
        stats  = self._load_stats()
        score  = self._calc_score(stats)
        passed = score >= 0.70

        logger.info(
            f"  : {' ' if passed else ' '} "
            f"(={score:.1%})"
        )
        return passed, score, stats

    def _load_stats(self) -> Dict:
        if not self.db_path.exists():
            return {}
        try:
            conn  = sqlite3.connect(self.db_path)
            cur   = conn.cursor()

            cur.execute("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM trade_history WHERE side='BUY'")
            row         = cur.fetchone()
            total_buys  = row[0] or 0
            first_ts    = row[1] or ""
            last_ts     = row[2] or ""

            cur.execute("""
                SELECT profit_rate FROM trade_history
                WHERE side='SELL' AND profit_rate IS NOT NULL
            """)
            rates = [r[0] for r in cur.fetchall() if r[0] is not None]
            conn.close()

            if not rates:
                return {"total_trades": total_buys}

            arr      = np.array(rates)
            wins     = arr[arr > 0]
            losses   = arr[arr <= 0]
            win_rate = len(wins) / len(arr)

            sharpe = 0.0
            if arr.std() > 0:
                sharpe = float(arr.mean() / arr.std() * (252 ** 0.5))

            cum    = np.cumprod(1 + arr)
            peak   = np.maximum.accumulate(cum)
            mdd    = float(((cum - peak) / peak).min()) if len(cum) > 0 else 0

            expectancy = (
                win_rate * float(wins.mean() if len(wins) > 0 else 0)
                + (1 - win_rate) * float(losses.mean() if len(losses) > 0 else 0)
            )

            trade_days = 0
            if first_ts and last_ts:
                try:
                    t1 = datetime.fromisoformat(first_ts.replace("T", " ")[:19])
                    t2 = datetime.fromisoformat(last_ts.replace("T", " ")[:19])
                    trade_days = (t2 - t1).days
                except Exception:
                    pass

            return {
                "total_trades": len(arr),
                "win_rate":     win_rate,
                "sharpe":       sharpe,
                "mdd":          mdd,
                "expectancy":   expectancy,
                "trade_days":   trade_days,
            }
        except Exception as e:
            logger.error(f"  : {e}")
            return {}

    def _calc_score(self, stats: Dict) -> float:
        if not stats:
            return 0.0

        checks = [
            (stats.get("win_rate",   0) >= 0.50, 0.25),
            (stats.get("sharpe",     0) >= 0.50, 0.25),
            (abs(stats.get("mdd",    0)) <= 0.10, 0.20),
            (stats.get("expectancy", 0) >= 0.001, 0.15),
            (stats.get("trade_days", 0) >= 14,    0.10),
            (stats.get("total_trades",0) >= 20,   0.05),
        ]
        return sum(w for ok, w in checks if ok)

    def print_report(self):
        passed, score, stats = self.check()
        print(f"""{'='*55}
     
  : {score:.1%} | {' ' if passed else ' '}
{'='*55}
     : {stats.get('total_trades', 0)}
         : {stats.get('win_rate',     0)*100:.1f}%  ( ≥ 50%)
     : {stats.get('sharpe',       0):.2f}   ( ≥ 0.5)
     : {stats.get('mdd',          0)*100:.1f}%  ( ≤ 10%)
       : {stats.get('expectancy',   0):+.4f} ( ≥ 0.001)
     : {stats.get('trade_days',   0)}   ( ≥ 14)
{'='*55}
{'      !' if passed else '       '}
{'='*55}""")


if __name__ == "__main__":
    LiveReadinessChecker().print_report()
