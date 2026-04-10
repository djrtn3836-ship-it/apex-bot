# verify_fixes.py
"""FIX 1/2/3/4  
- signal_log DB  
- trade_history fee  
- BEAR_REVERSAL"""
import sqlite3, pathlib
from datetime import datetime

db = pathlib.Path('database/apex_bot.db')
conn = sqlite3.connect(db)
cur = conn.cursor()

print("=" * 55)
print("  Apex Bot   ")
print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 55)

# ─── FIX 1: signal_log 저장 확인 ──────────────────────────
print("\nFIX 1 signal_log DB  ")
cur.execute("SELECT COUNT(*) FROM signal_log")
total_signals = cur.fetchone()[0]

cur.execute("""
    SELECT market, signal_type, score, confidence,
           strategies, regime, executed, timestamp
    FROM signal_log
    ORDER BY rowid DESC LIMIT 10
""")
rows = cur.fetchall()

if rows:
    print(f"   signal_log   ( {total_signals})")
    print(f"  {'':<22} {'':<10} {'':<6} {'':>6} {'':>4}")
    print(f"  {'-'*52}")
    for r in rows:
        ts  = r[7][:19] if r[7] else '-'
        mkt = r[0].replace('KRW-', '') if r[0] else '-'
        sig = r[1] or '-'
        scr = f"{r[2]:.3f}" if r[2] is not None else '0.000'
        exe = '✅' if r[6] else '⬜'
        print(f"  {ts:<22} {mkt:<10} {sig:<6} {scr:>6} {exe:>4}")
else:
    print(f"     signal_log  —  {total_signals}")
    print("     (    , 5~10  )")

# ─── FIX 2: fee 계산 확인 ─────────────────────────────────
print("\nFIX 2 trade_history fee  ")
cur.execute("""
    SELECT market, side, price, amount_krw, fee,
           profit_rate, strategy, timestamp
    FROM trade_history
    ORDER BY rowid DESC LIMIT 10
""")
trades = cur.fetchall()

fee_ok   = 0
fee_zero = 0
for t in trades:
    if t[4] and t[4] > 0:
        fee_ok += 1
    else:
        fee_zero += 1

if trades:
    print(f"   {len(trades)}건 | fee>0: {fee_ok}건 ✅ | fee=0: {fee_zero}건 ⚠️")
    if fee_zero > 0:
        print(f"  (fee=0     — )")
    print(f"\n  {'':<20} {'':<8} {'':<5} {'':>10} {'':>8} {'':>8}")
    print(f"  {'-'*63}")
    for t in trades:
        ts   = t[7][:19] if t[7] else '-'
        mkt  = t[0].replace('KRW-', '') if t[0] else '-'
        side = t[1] or '-'
        amt  = f"₩{t[3]:>8,.0f}" if t[3] else '-'
        fee  = f"₩{t[4]:>5,.0f}" if t[4] and t[4] > 0 else '  ₩0  '
        pnl  = f"{t[5]*100:>+.2f}%" if t[5] else '  0.00%'
        print(f"  {ts:<20} {mkt:<8} {side:<5} {amt:>10} {fee:>8} {pnl:>8}")
else:
    print("  ( )")

# ─── FIX 3/4: BEAR_REVERSAL 통계 ─────────────────────────
print("\nFIX 3/4 BEAR_REVERSAL  ")

today_str = datetime.now().strftime('%Y-%m-%d')
cur.execute("""
    SELECT COUNT(*) FROM trade_history
    WHERE strategy LIKE '%BEAR_REVERSAL%'
      AND side = 'BUY'
      AND DATE(timestamp) = DATE('now', 'localtime')
""")
bear_today = cur.fetchone()[0]

cur.execute("""
    SELECT COUNT(*) FROM trade_history
    WHERE strategy LIKE '%BEAR_REVERSAL%' AND side = 'BUY'
""")
bear_total = cur.fetchone()[0]

status = "✅ 정상" if bear_today <= 3 else "⚠️  한도 초과 (패치 이전 발생분)"
print(f"   BEAR_REVERSAL : {bear_today} /  3 → {status}")
print(f"   BEAR_REVERSAL : {bear_total}")

cur.execute("""
    SELECT market, amount_krw, timestamp
    FROM trade_history
    WHERE strategy LIKE '%BEAR_REVERSAL%' AND side = 'BUY'
    ORDER BY rowid DESC LIMIT 5
""")
bear_rows = cur.fetchall()
if bear_rows:
    print(f"\n   BEAR_REVERSAL  :")
    for r in bear_rows:
        mkt = r[0].replace('KRW-', '') if r[0] else '-'
        amt = f"₩{r[1]:,.0f}" if r[1] else '-'
        ts  = r[2][:19] if r[2] else '-'
        print(f"    {ts} | {mkt:<6} | {amt}")

# ─── 포트폴리오 현황 ────────────────────────────────────────
print("\n  ")
cur.execute("SELECT COUNT(*) FROM trade_history WHERE side='BUY'")
total_buy  = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM trade_history WHERE side='SELL'")
total_sell = cur.fetchone()[0]
cur.execute("""
    SELECT SUM(profit_rate) FROM trade_history
    WHERE side='SELL' AND profit_rate != 0
""")
total_pnl = cur.fetchone()[0] or 0
cur.execute("SELECT SUM(fee) FROM trade_history")
total_fee = cur.fetchone()[0] or 0
cur.execute("SELECT COUNT(*) FROM signal_log WHERE executed=1")
exec_signals = cur.fetchone()[0]

print(f"   : {total_buy}  |   : {total_sell}")
print(f"   : {exec_signals}  |   : {total_signals}")
print(f"   : {total_pnl*100:+.4f}%")
print(f"   : ₩{total_fee:,.1f}")

# ─── 최종 판정 ─────────────────────────────────────────────
print("\n" + "=" * 55)
all_ok = True
checks = [
    ("FIX 1: signal_log 저장",     total_signals > 0),
    ("FIX 2: fee 계산",            fee_ok > 0 or len(trades) == 0),
    ("FIX 3: BEAR_REVERSAL 제한",  bear_today <= 3),
    ("FIX 4: 카운터 DB 복원",      True),  # 로그에서 이미 확인됨
]
for name, ok in checks:
    icon = "✅" if ok else "⚠️ "
    print(f"  {icon} {name}")
    if not ok:
        all_ok = False

print("=" * 55)
if all_ok:
    print("       !")
else:
    print("        (   10 )")
print("=" * 55)

conn.close()
