# verify_all.py
import sqlite3

DB = r"database/apex_bot.db"
conn = sqlite3.connect(DB)
cur = conn.cursor()

print("=" * 65)
print(" APEX BOT 전체 상태 검증 (2026-04-29 15:39)")
print("=" * 65)

# ── 7. 열린 포지션 ──────────────────────────────────────────
print("\n[ 7. 열린 포지션 현황 ]")
cur.execute("""
    SELECT b.market, b.price, b.amount_krw, b.strategy, b.timestamp
    FROM trade_history b
    WHERE b.side='BUY'
      AND NOT EXISTS (
          SELECT 1 FROM trade_history s
          WHERE s.market=b.market AND s.side='SELL' AND s.timestamp > b.timestamp
      )
    ORDER BY b.timestamp DESC
""")
rows = cur.fetchall()
print(f"열린 포지션: {len(rows)}개")
print(f"{'코인':<15} {'진입가':>12} {'금액':>10} {'전략':<22} {'시간'}")
print("-" * 75)
for r in rows:
    flag = "⚠️" if any(s in r[0] for s in ["USDT","USDC","USD1"]) else "  "
    print(f"{flag}{r[0]:<13} {r[1]:>12,.1f} {r[2]:>9,.0f}원  {r[3]:<22} {r[4][11:19]}")

# ── 8. 오늘 실현 손익 ────────────────────────────────────────
print("\n[ 8. 오늘 실현 손익 (2026-04-29) ]")
cur.execute("""
    SELECT market, price, amount_krw, profit_rate, strategy, timestamp
    FROM trade_history
    WHERE side='SELL' AND timestamp LIKE '2026-04-29%'
    ORDER BY timestamp DESC
""")
rows = cur.fetchall()
if not rows:
    print("오늘 SELL 없음")
else:
    wins   = [r for r in rows if r[3] and r[3] > 0]
    losses = [r for r in rows if r[3] and r[3] <= 0]
    total_pnl = sum(r[2] * r[3] / 100 for r in rows if r[3])
    avg_win   = sum(r[3] for r in wins)   / len(wins)   if wins   else 0
    avg_loss  = sum(r[3] for r in losses) / len(losses) if losses else 0
    rr        = abs(avg_win / avg_loss) if avg_loss else 0
    ev        = (len(wins)/len(rows)) * avg_win + (len(losses)/len(rows)) * avg_loss
    print(f"총 거래: {len(rows)}건 | 승: {len(wins)}건 | 패: {len(losses)}건 | 승률: {len(wins)/len(rows)*100:.1f}%")
    print(f"평균 수익: +{avg_win:.3f}% | 평균 손실: {avg_loss:.3f}% | 손익비: {rr:.2f}x | 기대값: {ev:+.3f}%")
    print(f"오늘 순손익: {total_pnl:+,.0f}원")
    print()
    print(f"{'코인':<15} {'체결가':>12} {'금액':>9} {'수익률':>8} {'전략':<22} {'시간'}")
    print("-" * 80)
    for r in rows[:30]:
        flag = "✅" if r[3] and r[3] > 0 else "❌"
        print(f"{flag} {r[0]:<13} {r[1]:>12,.1f} {r[2]:>8,.0f}원 {r[3]:>+7.2f}%  {r[4]:<22} {r[5][11:19]}")

# ── 9. 최근 7일 일별 성과 ────────────────────────────────────
print("\n[ 9. 최근 7일 일별 성과 ]")
cur.execute("""
    SELECT date, total_assets, daily_pnl, trade_count, win_count,
           win_rate, max_drawdown, sharpe_ratio
    FROM daily_performance ORDER BY date DESC LIMIT 7
""")
rows = cur.fetchall()
print(f"{'날짜':<12} {'총자산':>13} {'일손익':>11} {'거래':>5} {'승률':>7} {'MDD':>8} {'Sharpe':>7}")
print("-" * 68)
for r in rows:
    asset = f"{r[1]:>12,.0f}원" if r[1] else "           0원"
    pnl   = f"{r[2]:>+10,.0f}원" if r[2] else "          0원"
    wr    = f"{r[5]*100:>6.1f}%" if r[5] else "   N/A"
    mdd   = f"{r[6]*100:>7.2f}%" if r[6] else "    N/A"
    shrp  = f"{r[7]:>6.2f}"      if r[7] else "   N/A"
    print(f"{r[0]:<12} {asset} {pnl} {r[3]:>5} {wr} {mdd} {shrp}")

# ── 10. 전략별 누적 성과 ─────────────────────────────────────
print("\n[ 10. 전략별 누적 성과 (전체 기간) ]")
cur.execute("""
    SELECT strategy,
           COUNT(*) as total,
           SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END) as wins,
           ROUND(AVG(profit_rate), 3) as avg_pnl,
           ROUND(SUM(amount_krw * profit_rate / 100), 0) as cum_krw,
           ROUND(AVG(CASE WHEN profit_rate > 0 THEN profit_rate END), 3) as avg_win,
           ROUND(AVG(CASE WHEN profit_rate <= 0 THEN profit_rate END), 3) as avg_loss
    FROM trade_history
    WHERE side='SELL' AND profit_rate IS NOT NULL
    GROUP BY strategy ORDER BY cum_krw DESC
""")
rows = cur.fetchall()
print(f"{'전략':<25} {'거래':>5} {'승률':>7} {'평균':>8} {'누적손익':>12} {'평균TP':>8} {'평균SL':>8}")
print("-" * 78)
for r in rows:
    wr   = f"{r[2]/r[1]*100:.1f}%" if r[1] else "N/A"
    awin = f"+{r[5]:.3f}%"         if r[5] else "   N/A"
    alss = f"{r[6]:.3f}%"          if r[6] else "   N/A"
    print(f"{r[0]:<25} {r[1]:>5} {wr:>7} {r[3]:>+7.3f}%  {r[4]:>10,.0f}원  {awin:>8} {alss:>8}")

# ── 11. 패치 전후 비교 ───────────────────────────────────────
print("\n[ 11. ATR 패치 전후 손익비 비교 ]")
for label, cond in [
    ("패치 전 (~ 06:43)", "timestamp < '2026-04-29 06:43:00'"),
    ("패치 후 (06:43 ~)", "timestamp >= '2026-04-29 06:43:00'"),
]:
    cur.execute(f"""
        SELECT COUNT(*),
               ROUND(AVG(CASE WHEN profit_rate > 0 THEN profit_rate END), 3),
               ROUND(AVG(CASE WHEN profit_rate <= 0 THEN profit_rate END), 3),
               ROUND(SUM(amount_krw * profit_rate / 100), 0)
        FROM trade_history
        WHERE side='SELL' AND profit_rate IS NOT NULL AND {cond}
    """)
    r = cur.fetchone()
    if r[0]:
        rr = abs(r[1]/r[2]) if r[2] else 0
        ev = 0
        cur2 = conn.cursor()
        cur2.execute(f"""
            SELECT COUNT(*),
                   SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END)
            FROM trade_history
            WHERE side='SELL' AND profit_rate IS NOT NULL AND {cond}
        """)
        t, w = cur2.fetchone()
        wr = w/t if t else 0
        ev = wr * (r[1] or 0) + (1-wr) * (r[2] or 0)
        print(f"[{label}] 거래:{r[0]:>3}건 | 평균TP:{r[1]:>+6.3f}% | 평균SL:{r[2]:>7.3f}% | 손익비:{rr:.2f}x | EV:{ev:+.3f}% | 누적:{r[3]:>+7,.0f}원")
    else:
        print(f"[{label}] 거래 없음")

# ── 12. bot_state 핵심 ───────────────────────────────────────
print("\n[ 12. bot_state 핵심 지표 ]")
import json
cur.execute("SELECT key, value, updated_at FROM bot_state ORDER BY key")
for key, val, upd in cur.fetchall():
    if key == "walk_forward_last_result":
        try:
            p = json.loads(val)
            print(f"[walk_forward] updated={upd}")
            for k, v in list(p.items())[:6]:
                print(f"  {k}: {v}")
        except:
            print(f"[walk_forward] {str(val)[:60]}")
    elif key == "sell_cooldown":
        try:
            p = json.loads(val)
            print(f"[sell_cooldown] {len(p)}개 항목 | 최신갱신: {upd}")
        except:
            print(f"[sell_cooldown] {upd}")

conn.close()
print("\n" + "=" * 65)
print(" 검증 완료")
print("=" * 65)
