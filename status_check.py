import os, sqlite3
from datetime import datetime

base = os.path.dirname(os.path.abspath(__file__))
db_path = os.path.join(base, "database", "apex_bot.db")
now = datetime.now()
print(f"=== APEX BOT 현황 ({now.strftime('%Y-%m-%d %H:%M:%S')}) ===\n")

conn = sqlite3.connect(db_path)
cur  = conn.cursor()

# 1. 열린 포지션
print("[ 열린 포지션 ]")
cur.execute("""
    SELECT b.market, b.price, b.timestamp, b.strategy
    FROM trade_history b
    LEFT JOIN trade_history s
        ON b.market = s.market
       AND s.side = 'SELL'
       AND s.timestamp > b.timestamp
    WHERE b.side = 'BUY'
      AND b.mode IN ('paper','live')
      AND s.id IS NULL
    ORDER BY b.timestamp ASC
""")
rows = cur.fetchall()
total_invested = 0
for market, price, ts, strategy in rows:
    entry_dt = datetime.fromisoformat(ts)
    held_h   = (now - entry_dt).total_seconds() / 3600
    is_surge = "SURGE" in (strategy or "")
    print(f"  {market:<12} 매수가={price:>10,.2f} | 보유={held_h:.1f}h | 전략={strategy} | SURGE={is_surge}")
if not rows:
    print("  (없음)")

# 2. 오늘 거래 요약
print("\n[ 오늘 거래 내역 ]")
cur.execute("""
    SELECT side, market, price, profit_rate, reason, timestamp
    FROM trade_history
    WHERE DATE(timestamp) = DATE('now','localtime')
    ORDER BY timestamp ASC
""")
trades = cur.fetchall()
buy_cnt = sell_cnt = 0
total_pnl = 0.0
for side, market, price, profit_rate, reason, ts in trades:
    pnl = profit_rate or 0.0
    if side == 'BUY':
        buy_cnt += 1
        print(f"  BUY  {market:<12} {ts[11:19]}  전략/사유={reason}")
    else:
        sell_cnt += 1
        total_pnl += pnl
        print(f"  SELL {market:<12} {ts[11:19]}  {pnl:+.3f}%  사유={reason}")
print(f"\n  매수 {buy_cnt}건 / 매도 {sell_cnt}건 / 실현손익 합계 {total_pnl:+.3f}%")

# 3. 오늘 손절 현황
print("\n[ 오늘 손절 내역 ]")
cur.execute("""
    SELECT market, profit_rate, reason, timestamp
    FROM trade_history
    WHERE DATE(timestamp) = DATE('now','localtime')
      AND side = 'SELL'
      AND profit_rate < 0
    ORDER BY profit_rate ASC
""")
losses = cur.fetchall()
for market, pnl, reason, ts in losses:
    print(f"  {market:<12} {pnl:+.3f}%  {reason}  {ts[11:19]}")
if not losses:
    print("  (없음)")

# 4. 연속 손실 카운터
print("\n[ 연속 손실 / 서킷브레이커 ]")
cur.execute("""
    SELECT value FROM bot_state
    WHERE key IN ('consecutive_loss','circuit_breaker')
    ORDER BY key
""")
for row in cur.fetchall():
    print(f"  {row[0]}")

# 5. SL 쿨다운
print("\n[ SL 쿨다운 중인 코인 ]")
cur.execute("""
    SELECT key, value FROM bot_state
    WHERE key LIKE 'sl_cooldown_%'
""")
cooldowns = cur.fetchall()
for key, val in cooldowns:
    market = key.replace("sl_cooldown_", "")
    try:
        until = datetime.fromisoformat(val)
        rem   = int((until - now).total_seconds() // 60)
        if rem > 0:
            print(f"  {market:<12} {rem}분 남음 (까지={val[:19]})")
    except:
        pass
if not cooldowns:
    print("  (없음)")

conn.close()
print("\n=== 확인 완료 ===")
