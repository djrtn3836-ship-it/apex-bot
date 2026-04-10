"""
APEX BOT Performance Analysis
Usage: python analyze_performance.py
Note: profit_rate in DB is a decimal (0.01 = 1%), multiply by 100 to display.
"""
import sqlite3
from pathlib import Path
from datetime import datetime

DB = Path("database/apex_bot.db")
if not DB.exists():
    print("DB not found:", DB)
    raise SystemExit(1)

try:
    from config.settings import settings as _settings
    INITIAL_CAPITAL = getattr(_settings, "INITIAL_CAPITAL",
                     getattr(_settings, "initial_capital", 1_000_000))
except Exception:
    INITIAL_CAPITAL = 1_000_000

with sqlite3.connect(DB) as conn:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("=" * 58)
    print("  APEX BOT Performance Summary")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 58)

    # Section 1: Overall stats
    cur.execute("""
        SELECT
            COUNT(*)                                                           AS total,
            SUM(CASE WHEN side='BUY'  THEN 1 ELSE 0 END)                    AS buys,
            SUM(CASE WHEN side='SELL' THEN 1 ELSE 0 END)                    AS sells,
            SUM(CASE WHEN side='SELL' AND profit_rate > 0  THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN side='SELL' AND profit_rate <= 0 THEN 1 ELSE 0 END) AS losses,
            ROUND(AVG(CASE WHEN side='SELL' THEN profit_rate END) * 100, 3)  AS avg_pct,
            ROUND(MAX(CASE WHEN side='SELL' THEN profit_rate END) * 100, 3)  AS max_pct,
            ROUND(MIN(CASE WHEN side='SELL' THEN profit_rate END) * 100, 3)  AS worst_pct,
            ROUND(SUM(CASE WHEN side='SELL'
                      THEN profit_rate * amount_krw ELSE 0 END), 0)           AS net_pnl
        FROM trade_history
    """)
    row = cur.fetchone()
    t_sells = (row["wins"] or 0) + (row["losses"] or 0)
    wr = round((row["wins"] or 0) / t_sells * 100, 1) if t_sells > 0 else 0.0

    print(f"\n  Total trades : {row['total']} (Buy {row['buys']}, Sell {row['sells']})")
    print(f"  Win rate     : {wr}%  (Win {row['wins']}, Loss {row['losses']})")
    print(f"  Avg return   : {row['avg_pct']}%")
    print(f"  Best trade   : {row['max_pct']}%")
    print(f"  Worst trade  : {row['worst_pct']}%")
    print(f"  Net PnL      : {row['net_pnl']:+,.0f} KRW")
    print(f"  Return       : {(row['net_pnl'] or 0)/INITIAL_CAPITAL*100:+.2f}% (initial {INITIAL_CAPITAL:,})")

    # Section 2: Strategy breakdown
    print(f"\n  === Strategy Breakdown ===")
    cur.execute("""
        SELECT
            strategy,
            COUNT(*)                                                           AS cnt,
            ROUND(AVG(CASE WHEN side='SELL' THEN profit_rate END) * 100, 3) AS avg_pct,
            SUM(CASE WHEN side='SELL' AND profit_rate > 0  THEN 1 ELSE 0 END) AS w,
            SUM(CASE WHEN side='SELL' AND profit_rate <= 0 THEN 1 ELSE 0 END) AS l,
            ROUND(SUM(CASE WHEN side='SELL'
                      THEN profit_rate * amount_krw ELSE 0 END), 0)           AS pnl
        FROM trade_history
        GROUP BY strategy
        ORDER BY avg_pct DESC
    """)
    for r in cur.fetchall():
        t2  = (r["w"] or 0) + (r["l"] or 0)
        wr2 = round((r["w"] or 0) / t2 * 100) if t2 > 0 else 0
        print(f"    {r['strategy']:<24} {r['cnt']:>3} trades | "
              f"avg {r['avg_pct']:>7}% | wr {wr2:>3}% | pnl {r['pnl']:>+10,.0f}")

    # Section 3: Coin breakdown
    print(f"\n  === Coin Breakdown ===")
    cur.execute("""
        SELECT
            market,
            COUNT(*) AS cnt,
            ROUND(SUM(CASE WHEN side='SELL'
                      THEN profit_rate * amount_krw ELSE 0 END), 0) AS pnl
        FROM trade_history
        GROUP BY market
        ORDER BY pnl DESC
    """)
    for r in cur.fetchall():
        icon = "+" if (r["pnl"] or 0) > 0 else "-"
        print(f"    [{icon}] {r['market']:<12} {r['cnt']:>3} trades | pnl {r['pnl']:>+10,.0f}")

    print("=" * 58)
