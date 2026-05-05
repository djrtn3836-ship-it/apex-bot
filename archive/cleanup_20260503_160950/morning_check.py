# morning_check.py – 내일 아침 Phase2+3 복합 효과 검증
import sqlite3
from datetime import datetime, date

conn = sqlite3.connect('database/apex_bot.db')
c = conn.cursor()
today = date.today().isoformat()

print("=" * 68)
print(f"  [ Apex Bot 아침 검증 리포트 – {today} ]")
print("=" * 68)

# ── 1. 현재 보유 포지션 ─────────────────────────────────────────
print("\n[ 1. 현재 보유 포지션 ]")
c.execute("""
    SELECT b.market, b.price, b.amount_krw, b.strategy, b.timestamp,
           round((julianday('now','localtime') - julianday(b.timestamp)) * 24, 2) as held_hours
    FROM trade_history b
    WHERE b.side = 'BUY'
    AND NOT EXISTS (
        SELECT 1 FROM trade_history s
        WHERE s.market = b.market AND s.side = 'SELL'
        AND s.timestamp > b.timestamp
    )
    ORDER BY held_hours DESC
""")
rows = c.fetchall()
surge_cnt = sum(1 for r in rows if 'SURGE' in str(r[3]))
boll_cnt  = sum(1 for r in rows if 'Bollinger' in str(r[3]))
ob_cnt    = sum(1 for r in rows if 'Order_Block' in str(r[3]))
macd_cnt  = sum(1 for r in rows if 'MACD' in str(r[3]))
print(f"총 {len(rows)}개 | SURGE:{surge_cnt} Bollinger:{boll_cnt} Order_Block:{ob_cnt} MACD:{macd_cnt}")
print(f"{'코인':<14} {'진입가':>10} {'금액':>9} {'전략':<22} {'보유'}")
print("-" * 68)
for market, price, amt, strat, ts, held in rows:
    warn = "⚠️ " if held >= 4 and 'SURGE' in str(strat) else "   "
    print(f"{warn}{market:<12} {price:>10,.2f} {amt:>9,.0f}  {str(strat):<22} {held:.1f}h")

# ── 2. 오늘 실현 손익 ───────────────────────────────────────────
print(f"\n[ 2. 오늘 실현 손익 ({today}) ]")
c.execute("""
    SELECT count(*),
           sum(case when profit_rate > 0 then 1 else 0 end),
           sum(case when profit_rate < 0 then 1 else 0 end),
           round(avg(case when profit_rate > 0 then profit_rate end), 4),
           round(avg(case when profit_rate < 0 then profit_rate end), 4),
           round(sum(profit_rate * amount_krw / 100), 0)
    FROM trade_history
    WHERE side='SELL' AND date(timestamp) = ?
""", (today,))
row = c.fetchone()
total, wins, losses, avg_win, avg_loss, net = row
wins = wins or 0; losses = losses or 0
avg_win = avg_win or 0; avg_loss = avg_loss or 0; net = net or 0
wr  = round(wins/total*100, 1) if total else 0
rr  = round(abs(avg_win/avg_loss), 2) if avg_loss else 0
ev  = round((wr/100*avg_win) + ((1-wr/100)*avg_loss), 4) if total else 0
print(f"거래 {total}건 | 승 {wins} 패 {losses} | 승률 {wr}%")
print(f"평균수익 {avg_win:+.3f}% | 평균손실 {avg_loss:+.3f}% | 손익비 {rr}x | EV {ev:+.4f}%")
print(f"순손익: {net:+,.0f} KRW")

# ── 3. 전략별 오늘 성과 ─────────────────────────────────────────
print(f"\n[ 3. 전략별 오늘 성과 ]")
c.execute("""
    SELECT strategy,
           count(*) as cnt,
           sum(case when profit_rate > 0 then 1 else 0 end) as wins,
           round(avg(profit_rate), 4) as avg_pnl,
           round(sum(profit_rate * amount_krw / 100), 0) as net_krw,
           round(avg(amount_krw), 0) as avg_size
    FROM trade_history
    WHERE side='SELL' AND date(timestamp) = ?
    GROUP BY strategy
    ORDER BY net_krw DESC
""", (today,))
rows2 = c.fetchall()
print(f"{'전략':<22} {'건수':>4} {'승률':>7} {'평균%':>8} {'순손익':>10} {'평균크기':>9}")
print("-" * 65)
for strat, cnt, w, avg_pnl, net2, avg_sz in rows2:
    w = w or 0
    wr2 = round(w/cnt*100, 1) if cnt else 0
    print(f"{str(strat):<22} {cnt:>4} {wr2:>6.1f}%  {avg_pnl:>+7.3f}%  {net2:>+10,.0f}  {avg_sz:>9,.0f}")

# ── 4. Phase2 검증 – 전략별 슬롯 배분 변화 ─────────────────────
print(f"\n[ 4. Phase2 검증 – 전략별 거래 비중 ]")
if rows2 and total > 0:
    for strat, cnt, w, avg_pnl, net2, avg_sz in rows2:
        pct = round(cnt/total*100, 1)
        bar = "█" * int(pct/5)
        print(f"  {str(strat):<22} {pct:>5.1f}% {bar}")
print("  목표: SURGE<30% / Bollinger>15% / Order_Block>15%")

# ── 5. Phase3 검증 – 포지션 크기 변화 ──────────────────────────
print(f"\n[ 5. Phase3 검증 – 전략별 평균 포지션 크기 ]")
c.execute("""
    SELECT strategy,
           round(avg(amount_krw), 0) as avg_size,
           round(min(amount_krw), 0) as min_size,
           round(max(amount_krw), 0) as max_size,
           count(*) as cnt
    FROM trade_history
    WHERE side='BUY' AND date(timestamp) = ?
    GROUP BY strategy
    ORDER BY avg_size DESC
""", (today,))
rows3 = c.fetchall()
print(f"{'전략':<22} {'평균':>9} {'최소':>9} {'최대':>9} {'건수':>5}")
print("-" * 58)
for strat, avg_s, min_s, max_s, cnt in rows3:
    print(f"{str(strat):<22} {avg_s:>9,.0f} {min_s:>9,.0f} {max_s:>9,.0f} {cnt:>5}")
print("  목표: Bollinger/Order_Block > SURGE 포지션 크기")

# ── 6. Phase2+3 복합 효과 – 어제 대비 ──────────────────────────
print(f"\n[ 6. 어제 vs 오늘 비교 ]")
c.execute("""
    SELECT date(timestamp) as d,
           count(*) as cnt,
           sum(case when profit_rate > 0 then 1 else 0 end) as wins,
           round(avg(case when profit_rate > 0 then profit_rate end), 4) as avg_win,
           round(avg(case when profit_rate < 0 then profit_rate end), 4) as avg_loss,
           round(sum(profit_rate * amount_krw / 100), 0) as net
    FROM trade_history
    WHERE side='SELL'
      AND date(timestamp) >= date('now', '-1 days')
    GROUP BY d
    ORDER BY d
""")
rows4 = c.fetchall()
print(f"{'날짜':<12} {'거래':>5} {'승률':>7} {'평균수익':>9} {'평균손실':>9} {'손익비':>7} {'순손익':>10}")
print("-" * 65)
for d, cnt, w, aw, al, net2 in rows4:
    w = w or 0; aw = aw or 0; al = al or 0; net2 = net2 or 0
    wr3 = round(w/cnt*100, 1) if cnt else 0
    rr3 = round(abs(aw/al), 2) if al else 0
    print(f"{d:<12} {cnt:>5} {wr3:>6.1f}%  {aw:>+8.3f}%  {al:>+8.3f}%  {rr3:>6.2f}x  {net2:>+10,.0f}")

# ── 7. Kelly 현황 – 전략별 실제 포지션 크기 추이 ───────────────
print(f"\n[ 7. 전략별 Kelly 효과 (최근 7일) ]")
c.execute("""
    SELECT strategy,
           count(*) as cnt,
           round(avg(amount_krw), 0) as avg_size,
           round(min(amount_krw), 0) as min_size,
           round(max(amount_krw), 0) as max_size
    FROM trade_history
    WHERE side='BUY'
      AND timestamp >= datetime('now', '-7 days')
    GROUP BY strategy
    ORDER BY avg_size DESC
""")
rows5 = c.fetchall()
print(f"{'전략':<22} {'7일거래':>7} {'평균크기':>9} {'최소':>9} {'최대':>9}")
print("-" * 60)
for strat, cnt, avg_s, min_s, max_s in rows5:
    print(f"{str(strat):<22} {cnt:>7} {avg_s:>9,.0f} {min_s:>9,.0f} {max_s:>9,.0f}")

# ── 8. 봇 상태 핵심 지표 ────────────────────────────────────────
print(f"\n[ 8. 봇 상태 핵심 지표 ]")
c.execute("""
    SELECT key, value, updated_at FROM bot_state
    WHERE key IN (
        'sell_cooldown', 'walk_forward_last_result',
        'consecutive_loss_count', 'circuit_breaker_active'
    )
""")
for key, val, upd in c.fetchall():
    print(f"  {key:<30} = {str(val)[:50]} ({upd})")

# ── 9. 누적 전략 성과 (전체기간) ────────────────────────────────
print(f"\n[ 9. 누적 전략 성과 (전체기간) ]")
c.execute("""
    SELECT strategy,
           count(*) as cnt,
           sum(case when profit_rate > 0 then 1 else 0 end) as wins,
           round(avg(profit_rate), 4) as avg_pnl,
           round(sum(profit_rate * amount_krw / 100), 0) as net_krw
    FROM trade_history
    WHERE side='SELL'
    GROUP BY strategy
    ORDER BY net_krw DESC
""")
rows6 = c.fetchall()
total_net = sum(r[4] or 0 for r in rows6)
print(f"{'전략':<22} {'건수':>5} {'승률':>7} {'평균%':>8} {'누적손익':>12}")
print("-" * 58)
for strat, cnt, w, avg_pnl, net2 in rows6:
    w = w or 0; net2 = net2 or 0
    wr4 = round(w/cnt*100, 1) if cnt else 0
    print(f"{str(strat):<22} {cnt:>5} {wr4:>6.1f}%  {avg_pnl:>+7.3f}%  {net2:>+12,.0f}")
print("-" * 58)
print(f"{'전체 합계':<22} {'':>5} {'':>7} {'':>8} {total_net:>+12,.0f}")

print("\n" + "=" * 68)
conn.close()
