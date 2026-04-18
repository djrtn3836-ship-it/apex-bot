import sqlite3
from datetime import datetime

conn = sqlite3.connect(r'database/apex_bot.db')
cur = conn.cursor()

print(f"=== APEX BOT 점검 [{datetime.now().strftime('%Y-%m-%d %H:%M')}] ===")
print()

# 1. 봇 마지막 활동
cur.execute("SELECT MAX(timestamp) FROM trade_history")
last = cur.fetchone()[0]
print(f"[봇] 마지막 거래: {last[:16] if last else '없음'}")

# 2. 오늘 성과
today = datetime.now().strftime('%Y-%m-%dT00:00:00')
cur.execute("""
    SELECT COUNT(*), SUM(CASE WHEN profit_rate>0 THEN 1 ELSE 0 END),
           SUM(profit_rate/100.0*amount_krw)
    FROM trade_history WHERE side='SELL' AND timestamp >= ?
""", (today,))
r = cur.fetchone()
total, wins, pnl = r[0] or 0, r[1] or 0, r[2] or 0
print(f"[오늘] 승률: {wins}/{total} = {wins/total*100:.1f}%" if total > 0 else "[오늘] SELL 없음")
print(f"[오늘] 실현손익: KRW{pnl:+,.0f}")

# 3. 수정 후 누적
cur.execute("""
    SELECT COUNT(*), SUM(CASE WHEN profit_rate>0 THEN 1 ELSE 0 END),
           SUM(profit_rate/100.0*amount_krw)
    FROM trade_history WHERE side='SELL' AND timestamp >= '2026-04-16T21:37:00'
""")
r = cur.fetchone()
t2, w2, p2 = r[0] or 0, r[1] or 0, r[2] or 0
print()
print(f"[누적] 승률: {w2}/{t2} = {w2/t2*100:.1f}%" if t2 > 0 else "[누적] 없음")
print(f"[누적] 실현손익: KRW{p2:+,.0f}")

# 4. PPO 버퍼
cur.execute("SELECT COUNT(*) FROM trade_history WHERE side='SELL'")
ts = cur.fetchone()[0]
remain = max(0, 200 - ts)
print()
print(f"[PPO] {ts}/200 ({min(ts/200*100,100):.1f}%) | {'첫 학습 완료!' if ts>=200 else f'{remain}건 남음'}")

# 5. 켈리 활성화
print()
print("[켈리] 전략별 현황:")
cur.execute("""
    SELECT strategy, COUNT(*) as cnt,
           SUM(CASE WHEN profit_rate>0 THEN 1 ELSE 0 END)*100.0/COUNT(*) as wr
    FROM trade_history
    WHERE side='SELL' AND timestamp >= '2026-04-16T21:37:00'
    GROUP BY strategy ORDER BY cnt DESC LIMIT 8
""")
for r in cur.fetchall():
    status = "★활성화!" if r[1] >= 20 else f"{r[1]}/20"
    bar = "=" * r[1] + "-" * (20 - r[1])
    print(f"  [{bar}] {status:>8} {str(r[0]):<25} 승률{r[2]:>5.1f}%")

# 6. 오픈 포지션
print()
cur.execute("""
    SELECT b.market, b.price, b.amount_krw, b.timestamp, b.strategy
    FROM trade_history b
    WHERE b.side='BUY'
    AND b.market NOT IN (
        SELECT s.market FROM trade_history s
        WHERE s.side='SELL' AND s.id > b.id
    )
    AND b.timestamp >= '2026-04-16T21:37:00'
    ORDER BY b.timestamp DESC
""")
positions = cur.fetchall()
print(f"[포지션] {len(positions)}개 오픈")
for p in positions:
    print(f"  {p[0]:<12} 진입가={p[1]:>10,.1f} | KRW{p[2]:>8,.0f} | {p[3][:16]} | {p[4]}")

# 7. 최근 SELL 7건
print()
print("[최근 SELL 7건]")
cur.execute("""
    SELECT timestamp, market, profit_rate, amount_krw, reason
    FROM trade_history WHERE side='SELL'
    ORDER BY id DESC LIMIT 7
""")
for r in cur.fetchall():
    w = "WIN " if r[2] > 0 else "LOSS"
    pnl_krw = r[2]/100*r[3]
    print(f"  [{w}] {r[0][:16]} | {r[1]:<12} | {r[2]:>+.2f}% | {pnl_krw:>+,.0f}원 | {r[4]}")

# 8. CARV 반복손절 경고
cur.execute("""
    SELECT COUNT(*) FROM trade_history
    WHERE side='SELL' AND market='KRW-CARV'
    AND timestamp >= ? AND profit_rate < 0
""", (today,))
carv_loss = cur.fetchone()[0]
if carv_loss >= 2:
    print()
    print(f"[경고] KRW-CARV 오늘 {carv_loss}회 손절! 과매매 의심")

conn.close()
