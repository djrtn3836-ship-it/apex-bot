import sqlite3, json, os
from datetime import datetime

conn = sqlite3.connect('database/apex_bot.db')
cur  = conn.cursor()
now  = datetime.now()
today = now.strftime('%Y-%m-%d')

print('=' * 60)
print(f'APEX BOT 심층 일간 리포트')
print(f'생성 시각: {now.strftime("%Y-%m-%d %H:%M")}')
print('=' * 60)

# ── 1. 오늘 실현손익 (원금 착시 제거) ─────────────────────
print('\n【1】 오늘 실현손익 (원금 착시 제거)')
cur.execute("""
    SELECT market, strategy,
           ROUND(profit_rate,3),
           amount_krw,
           ROUND(profit_rate * amount_krw / 100, 0),
           timestamp
    FROM trade_history
    WHERE side='SELL' AND DATE(timestamp) = ?
    ORDER BY timestamp
""", (today,))
rows = cur.fetchall()
today_pnl = 0
if rows:
    for r in rows:
        pnl = r[4] or 0
        today_pnl += pnl
        icon = '🟢' if pnl >= 0 else '🔴'
        print(f'  {icon} {r[5][11:19]} | {r[0]:12s} | {str(r[1])[:18]:18s} '
              f'| {r[2]:+.3f}% | 원금:{int(r[3] or 0):,}원 | 손익:{int(pnl):+,}원')
    print(f'  {"─"*55}')
    print(f'  오늘 순손익: {int(today_pnl):+,}원  {"✅ 흑자" if today_pnl >= 0 else "🔴 적자"}')
else:
    print('  오늘 체결 없음')

# ── 2. 전략별 기대값 분석 ─────────────────────────────────
print('\n【2】 전략별 실제 기대값 (승률×평균수익 + 패율×평균손실)')
cur.execute("""
    SELECT strategy,
           COUNT(*) as cnt,
           SUM(CASE WHEN profit_rate>0 THEN 1 ELSE 0 END) as wins,
           ROUND(AVG(CASE WHEN profit_rate>0 THEN profit_rate END),3) as avg_win,
           ROUND(AVG(CASE WHEN profit_rate<=0 THEN profit_rate END),3) as avg_loss,
           ROUND(SUM(profit_rate * amount_krw / 100),0) as total_krw
    FROM trade_history
    WHERE side='SELL' AND DATE(timestamp) >= '2026-04-17'
    GROUP BY strategy HAVING cnt >= 3
    ORDER BY cnt DESC
""")
print(f'  {"전략":<22} {"건수":>4} {"승률":>6} {"기대값":>7} {"누적손익":>10} {"판정"}')
print(f'  {"─"*60}')
for r in cur.fetchall():
    name, cnt, wins, aw, al, total = r
    wr = wins/cnt if cnt else 0
    aw = aw or 0; al = al or 0
    ev = round(wr * aw + (1-wr) * al, 3)
    icon = '✅' if ev > 0.3 else ('⚠️ ' if ev > 0 else '🔴')
    print(f'  {icon} {str(name)[:20]:<20} {cnt:>4}건 '
          f'{wr*100:>5.0f}% {ev:>+7.3f}% {int(total):>+10,}원')

# ── 3. 시간대별 엣지 분석 ─────────────────────────────────
print('\n【3】 시간대별 엣지 분석')
cur.execute("""
    SELECT CAST(strftime('%H', timestamp) AS INT) / 6 as slot,
           COUNT(*) as cnt,
           SUM(CASE WHEN profit_rate>0 THEN 1 ELSE 0 END) as wins,
           ROUND(AVG(profit_rate),3) as avg_pnl,
           ROUND(SUM(profit_rate * amount_krw / 100),0) as total_krw
    FROM trade_history
    WHERE side='SELL' AND DATE(timestamp) >= '2026-04-17'
    GROUP BY slot ORDER BY slot
""")
slot_names = ['00~06시 (새벽)','06~12시 (오전)','12~18시 (오후)','18~24시 (저녁)']
for r in cur.fetchall():
    slot, cnt, wins, avg, total = r
    wr = wins/cnt*100 if cnt else 0
    icon = '✅' if wr >= 65 else ('⚠️ ' if wr >= 55 else '🔴')
    name = slot_names[slot] if slot < 4 else f'{slot*6}시대'
    print(f'  {icon} {name} | {cnt:3d}건 | 승률 {wr:.0f}% '
          f'| 평균 {avg:+.3f}% | 누적 {int(total):+,}원')

# ── 4. 보유 포지션 현황 (daily_performance + bot_state) ───
print('\n【4】 보유 포지션 현황')
# bot_state에서 포지션 정보 탐색
cur.execute("SELECT key, value FROM bot_state WHERE key LIKE '%position%' OR key LIKE '%portfolio%' ORDER BY key")
state_rows = cur.fetchall()
if state_rows:
    for key, val in state_rows:
        print(f'  📌 {key}: {str(val)[:80]}')
else:
    # daily_performance 최신값 사용
    cur.execute("""
        SELECT date, total_assets, daily_pnl, trade_count,
               win_count, open_positions
        FROM daily_performance
        ORDER BY date DESC LIMIT 5
    """)
    dp_rows = cur.fetchall()
    if dp_rows:
        print(f'  {"날짜":<12} {"총자산":>10} {"일손익":>8} {"거래":>5} {"승":>4} {"포지션":>6}')
        print(f'  {"─"*50}')
        for r in dp_rows:
            dt, assets, dpnl, tc, wc, op = r
            print(f'  {str(dt):<12} {int(assets or 0):>10,}원 '
                  f'{int(dpnl or 0):>+8,}원 {int(tc or 0):>5}건 '
                  f'{int(wc or 0):>4}승 {int(op or 0):>6}개')
    else:
        print('  daily_performance 데이터 없음')

# ── 5. MDD 추이 (일별) ────────────────────────────────────
print('\n【5】 MDD 추이 및 누적 손익')
cur.execute("""
    SELECT DATE(timestamp),
           ROUND(SUM(profit_rate * amount_krw / 100), 0)
    FROM trade_history
    WHERE side='SELL' AND DATE(timestamp) >= '2026-04-17'
    GROUP BY DATE(timestamp) ORDER BY DATE(timestamp)
""")
cum = peak = mdd = 0
print(f'  {"날짜":<12} {"일손익":>8} {"누적":>9} {"낙폭":>8} {"상태"}')
print(f'  {"─"*52}')
for dt, daily in cur.fetchall():
    cum += (daily or 0)
    if cum > peak: peak = cum
    dd = peak - cum
    if dd > mdd: mdd = dd
    state = '🔴 낙폭중' if dd > 0 else '✅ 신고점'
    print(f'  {dt:<12} {int(daily or 0):>+8,} {int(cum):>+9,} '
          f'{int(dd):>8,} {state}')
print(f'  {"─"*52}')
print(f'  역대 최대낙폭: {int(mdd):,}원 | '
      f'현재 {"낙폭 회복 중" if (peak-cum) > 0 else "✅ 신고점 갱신 중"}')

# ── 6. 연속 손실 리스크 ───────────────────────────────────
print('\n【6】 연속 손실 리스크')
cur.execute("""
    SELECT profit_rate FROM trade_history
    WHERE side='SELL' ORDER BY rowid DESC LIMIT 20
""")
streak = 0
for (pr,) in cur.fetchall():
    if (pr or 0) < 0: streak += 1
    else: break
limit = 5
icon = '✅' if streak == 0 else ('⚠️ ' if streak < 3 else '🔴')
print(f'  {icon} 현재 연속손실: {streak}회 | 한도까지 {limit-streak}회 여유')

# ── 7. 내일 리스크 예고 ───────────────────────────────────
print('\n【7】 내일 예상 리스크')
try:
    import urllib.request
    _url = "https://api.alternative.me/fng/?limit=1&format=json"
    with urllib.request.urlopen(_url, timeout=5) as _r:
        _item = json.loads(_r.read())["data"][0]
    fg_idx   = int(_item["value"])
    fg_label = _item["value_classification"]
    fg_icon  = '🔴' if fg_idx < 25 else ('⚠️ ' if fg_idx < 45 else '✅')
    print(f'  {fg_icon} FearGreed: {fg_idx} ({fg_label})')
    if fg_idx < 40:
        print(f'     → Vol_Breakout 차단 유지 (FG<40)')
        print(f'     → Order_Block 신뢰도 주의 구간')
    if fg_idx < 25:
        print(f'     → Extreme Fear: 신규 진입 전체 억제 권장')
    if fg_idx >= 75:
        print(f'     → Greed 구간: 포지션 축소 권장')
    if fg_idx >= 90:
        print(f'     → Extreme Greed 90+: 신규 매수 자동 차단 중')
except Exception as e:
    print(f'  ⚠️  FearGreed API 호출 실패: {e}')

# 최근 7일 일평균 거래 수
cur.execute("""
    SELECT ROUND(COUNT(*)*1.0 / COUNT(DISTINCT DATE(timestamp)), 1)
    FROM trade_history
    WHERE side='SELL' AND DATE(timestamp) >= date('now','-7 days')
""")
avg_daily = cur.fetchone()[0] or 0
print(f'  📊 최근 7일 일평균 거래: {avg_daily}건')

# 최근 signal_log 확인
cur.execute("""
    SELECT COUNT(*), SUM(executed)
    FROM signal_log WHERE DATE(timestamp) = ?
""", (today,))
sig_total, sig_exec = cur.fetchone()
print(f'  📡 오늘 신호: {int(sig_total or 0)}건 발생 | {int(sig_exec or 0)}건 실행')

print('\n' + '=' * 60)
print(f'오늘 청산: {len(rows)}건 | 순손익: {int(today_pnl):+,}원')
print(f'연속손실: {streak}회 | 역대 MDD: {int(mdd):,}원')
print('=' * 60)
conn.close()
