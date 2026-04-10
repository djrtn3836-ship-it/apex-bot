"""evening_check.py  v3
   +  
profit_rate DB  =  (0.01 = 1%)
: python evening_check.py"""
import sqlite3, pathlib, sys
from datetime import date

DB = pathlib.Path("database/apex_bot.db")
if not DB.exists():
    print(" DB :", DB); sys.exit(1)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()
today = date.today().isoformat()

print("=" * 62)
print(f" ({today}) 거래 요약")
print("=" * 62)

# 오늘 거래 집계
cur.execute("""
    SELECT side,
           COUNT(*)                    AS cnt,
           ROUND(SUM(amount_krw), 0)   AS total_krw,
           ROUND(AVG(profit_rate)*100, 2) AS avg_pct
    FROM trade_history
    WHERE DATE(timestamp) = ?
    GROUP BY side
""", (today,))
rows = cur.fetchall()
if not rows:
    print("     ")
else:
    for r in rows:
        sign = "+" if (r["avg_pct"] or 0) >= 0 else ""
        print(f"  {r['side']:4s} | {r['cnt']} | "
              f"₩{r['total_krw']:>12,.0f} |  {sign}{r['avg_pct']:.2f}%")

# SELL 상세
print("\n SELL  ")
cur.execute("""
    SELECT timestamp, market, price, volume,
           profit_rate, reason
    FROM trade_history
    WHERE side='SELL'
      AND DATE(timestamp)=?
    ORDER BY timestamp DESC
    LIMIT 20
""", (today,))
sells = cur.fetchall()
if not sells:
    print("   SELL  ")
else:
    for s in sells:
        pct  = (s["profit_rate"] or 0) * 100
        sign = "+" if pct >= 0 else ""
        print(f"  {str(s['timestamp'])[:19]}  {s['market']:<14} "
              f"₩{s['price']:>12,.0f}  {sign}{pct:.2f}%  [{s['reason']}]")

# 미결 포지션
print("\n   ")
cur.execute("""
    SELECT b.market,
           b.price      AS buy_price,
           b.volume     AS quantity,
           b.amount_krw AS invested,
           b.strategy
    FROM trade_history b
    LEFT JOIN trade_history s
        ON  b.market    = s.market
        AND s.side      = 'SELL'
        AND s.timestamp > b.timestamp
    WHERE b.side = 'BUY'
      AND b.mode = 'paper'
      AND s.id   IS NULL
    ORDER BY b.amount_krw DESC
""")
pos = cur.fetchall()
if not pos:
    print("    ")
else:
    total = 0.0
    for p in pos:
        print(f"  {p['market']:<14}   ₩{p['buy_price']:>12,.0f}  "
              f" {p['quantity']:.6f}   ₩{p['invested']:>10,.0f}  "
              f"[{p['strategy']}]")
        total += float(p["invested"])
    print(f"\n  {' ':14}  {'':<36}  ₩{total:>10,.0f}")

# 전체 누적 통계
print("\n    ")
cur.execute("""
    SELECT COUNT(*)                     AS total,
           SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END) AS wins,
           ROUND(AVG(profit_rate)*100, 2) AS avg_pct,
           ROUND(SUM(amount_krw * profit_rate), 0) AS total_pnl
    FROM trade_history
    WHERE side='SELL' AND mode='paper'
""")
stat = cur.fetchone()
if stat and stat["total"]:
    wr   = (stat["wins"] or 0) / stat["total"] * 100
    sign = "+" if (stat["avg_pct"] or 0) >= 0 else ""
    print(f"   SELL : {stat['total']} | "
          f" {wr:.1f}% | "
          f" {sign}{stat['avg_pct']:.2f}% | "
          f" P&L ₩{stat['total_pnl']:,.0f}")
else:
    print("  SELL  ")

con.close()
print("=" * 62)
