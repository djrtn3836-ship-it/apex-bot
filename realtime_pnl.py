"""realtime_pnl.py
trade_history     /  
: python realtime_pnl.py"""
import sqlite3, pathlib, sys, requests
from datetime import datetime

DB     = pathlib.Path("database/apex_bot.db")
SL_PCT = 0.97   # 매수가 × 0.97  (-3%)
TP_PCT = 1.05   # 매수가 × 1.05  (+5%)

if not DB.exists():
    print(" DB :", DB); sys.exit(1)

def get_price(market: str) -> float:
    try:
        r = requests.get(
            "https://api.upbit.com/v1/ticker",
            params={"markets": market}, timeout=5
        )
        return float(r.json()[0]["trade_price"])
    except Exception:
        return 0.0

def status_label(pct: float) -> str:
    if pct >=  3.0: return "🟢"
    if pct >=  0.5: return "🔵"
    if pct >= -1.0: return "🟠"
    if pct >= -3.0: return "🔴"
    return "⚪"

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

# 미결 포지션 = BUY 이후 SELL 없는 항목
cur.execute("""
    SELECT b.market,
           b.price      AS buy_price,
           b.volume     AS quantity,
           b.amount_krw AS invested,
           b.strategy,
           b.timestamp  AS buy_time
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
rows = cur.fetchall()
con.close()

if not rows:
    print("\n   (DB BUY    SELL )")
    sys.exit(0)

print(f"\n{'='*72}")
print(f"     ({datetime.now():%Y-%m-%d %H:%M:%S})")
print(f"{'='*72}")
print(f"  {'':<14} {'':>12} {'':>12} {'%':>7}  {'P&L':>10}  ")
print(f"  {'-'*66}")

total_invested = total_pnl = 0.0
details = []

for r in rows:
    market    = r["market"]
    buy_price = float(r["buy_price"])
    qty       = float(r["quantity"])
    invested  = float(r["invested"])

    cur_price = get_price(market)
    if cur_price == 0:
        cur_price = buy_price

    pct     = (cur_price - buy_price) / buy_price * 100
    pnl_krw = (cur_price - buy_price) * qty
    label   = status_label(pct)
    sign    = "+" if pct >= 0 else ""

    print(f"  {market:<14} {buy_price:>12,.0f} {cur_price:>12,.0f} "
          f"{sign}{pct:>6.2f}%  {pnl_krw:>+10,.0f}  {label}")

    total_invested += invested
    total_pnl      += pnl_krw
    details.append((market, buy_price, cur_price, qty))

total_pct  = total_pnl / total_invested * 100 if total_invested else 0
total_sign = "+" if total_pct >= 0 else ""
print(f"  {'-'*66}")
print(f"  {'':<14} {'':>25} {total_sign}{total_pct:.2f}%  {total_pnl:>+10,.0f}")

print(f"\n{''*72}")
print("  /   [   ]")
print(f"{''*72}")
for market, buy_price, cur_price, qty in details:
    sl      = buy_price * SL_PCT
    tp      = buy_price * TP_PCT
    sl_dist = (sl - cur_price) / cur_price * 100
    tp_dist = (tp - cur_price) / cur_price * 100
    sl_sign = "+" if sl_dist >= 0 else ""
    tp_sign = "+" if tp_dist >= 0 else ""
    print(f"  {market:<14}   {sl:>12,.0f} (  {sl_sign}{sl_dist:.1f}%)  "
          f"| 익절 {tp:>12,.0f} (현재가 대비 {tp_sign}{tp_dist:.1f}%)")

print(f"\n  ※  =  × {SL_PCT}  |   =  × {TP_PCT}")
print(f"{'='*72}\n")
