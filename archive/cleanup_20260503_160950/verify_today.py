# verify_today.py
import sqlite3

con = sqlite3.connect('database/apex_bot.db')
cur = con.cursor()

print('=== 오늘 DB 저장 거래내역 (2026-05-02) ===')
cur.execute(
    "SELECT timestamp, market, side, price, volume, amount_krw, fee, profit_rate, strategy "
    "FROM trade_history "
    "WHERE DATE(timestamp) = '2026-05-02' "
    "ORDER BY timestamp ASC"
)
rows = cur.fetchall()
print(f'총 {len(rows)}건')
print()

for r in rows:
    ts, market, side, price, vol, amt, fee, pnl, strat = r
    pnl = pnl or 0.0
    fee = fee or 0
    print(
        f"{str(ts)[:16]} | {str(side):4} | {str(market):12} | "
        f"가격={price:>10} | 수량={float(vol):>12.4f} | "
        f"금액={float(amt):>8,.0f} | 수수료={fee} | "
        f"수익률={float(pnl):>+6.2f}% | {strat}"
    )

con.close()
