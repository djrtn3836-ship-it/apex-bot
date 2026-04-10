"""
APEX BOT ?ъ링 ?깃낵 遺꾩꽍
?ㅽ뻾: python analyze_performance.py
profit_rate DB ??κ컪 = ?뚯닔 (0.01 = 1%) ??異쒕젰 ??x100 ?곸슜
"""
import sqlite3
from pathlib import Path
from datetime import datetime

DB = Path("database/apex_bot.db")
if not DB.exists():
    print("??DB ?놁쓬:", DB)
    raise SystemExit(1)

try:
    from config.settings import settings as _settings
    INITIAL_CAPITAL = getattr(_settings, "INITIAL_CAPITAL",
                     getattr(_settings, "initial_capital", 1_000_000))
except Exception:
    INITIAL_CAPITAL = 1_000_000  # fallback

with sqlite3.connect(DB) as conn:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("=" * 58)
    print("  ?뱤 APEX BOT ?ъ링 ?깃낵 遺꾩꽍")
    print(f"  湲곗?: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 58)

    # ?? 1. ?꾩껜 ?붿빟 ??????????????????????????????????????????????
    cur.execute("""
        SELECT
            COUNT(*)                                                          AS total,
            SUM(CASE WHEN side='BUY'  THEN 1 ELSE 0 END)                     AS buys,
            SUM(CASE WHEN side='SELL' THEN 1 ELSE 0 END)                     AS sells,
            SUM(CASE WHEN side='SELL' AND profit_rate > 0  THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN side='SELL' AND profit_rate <= 0 THEN 1 ELSE 0 END) AS losses,
            ROUND(AVG(CASE WHEN side='SELL' THEN profit_rate END) * 100, 3)   AS avg_pct,
            ROUND(MAX(CASE WHEN side='SELL' THEN profit_rate END) * 100, 3)   AS max_pct,
            ROUND(MIN(CASE WHEN side='SELL' THEN profit_rate END) * 100, 3)   AS worst_pct,
            ROUND(SUM(CASE WHEN side='SELL'
                      THEN profit_rate * amount_krw ELSE 0 END), 0)           AS net_pnl
        FROM trade_history
    """)
    row = cur.fetchone()
    t_sells = (row["wins"] or 0) + (row["losses"] or 0)
    wr = round((row["wins"] or 0) / t_sells * 100, 1) if t_sells > 0 else 0.0

    print(f"\n  珥?嫄곕옒   : {row['total']}嫄?(留ㅼ닔 {row['buys']}, 留ㅻ룄 {row['sells']})")
    print(f"  ?밸쪧      : {wr}%  (??{row['wins']}, ??{row['losses']})")
    print(f"  ?됯퇏 ?섏씡 : {row['avg_pct']}%")
    print(f"  理쒓퀬 ?섏씡 : {row['max_pct']}%")
    print(f"  理쒕? ?먯떎 : {row['worst_pct']}%")
    print(f"  ?쒖넀??   : ??row['net_pnl']:+,.0f}")
    print(f"  珥덇린?먮낯({INITIAL_CAPITAL:,}) ?鍮? {(row['net_pnl'] or 0)/INITIAL_CAPITAL*100:+.2f}%")

    # ?? 2. ?꾨왂蹂??????????????????????????????????????????????????
    print(f"\n  ?뱢 ?꾨왂蹂??깃낵")
    cur.execute("""
        SELECT
            strategy,
            COUNT(*)                                                           AS cnt,
            ROUND(AVG(CASE WHEN side='SELL' THEN profit_rate END) * 100, 3)   AS avg_pct,
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
        print(f"    {r['strategy']:<24} {r['cnt']:>3}嫄?| "
              f"?됯퇏{r['avg_pct']:>7}% | ?밸쪧{wr2:>3}% | ??r['pnl']:>+10,.0f}")

    # ?? 3. 醫낅ぉ蹂??????????????????????????????????????????????????
    print(f"\n  ?뮥 醫낅ぉ蹂??깃낵")
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
        icon = "?? if (r["pnl"] or 0) > 0 else "??
        print(f"    {icon} {r['market']:<12} {r['cnt']:>3}嫄?| ??r['pnl']:>+10,.0f}")

    print("=" * 58)

