import sqlite3, urllib.request, json
from datetime import datetime, date, timedelta

conn = sqlite3.connect('database/apex_bot.db')
cur = conn.cursor()
today = date.today().isoformat()

print('=' * 60)
print(f'  APEX BOT 데일리 체크  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
print('=' * 60)

# ── 1. 봇 프로세스 확인 ──────────────────────────────────
import subprocess
result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq python.exe'],
    capture_output=True, text=True)
bot_running = 'python.exe' in result.stdout
print(f'\n[봇 상태] {"✅ 실행 중" if bot_running else "🚨 중단됨!"}')

# ── 2. 현재 보유 포지션 + 실시간 손익 ───────────────────
print('\n[보유 포지션]')
cur.execute("""
    SELECT b.market, b.price, b.amount_krw, b.timestamp
    FROM trade_history b
    WHERE b.mode='paper' AND b.side='BUY'
    AND NOT EXISTS (
        SELECT 1 FROM trade_history s
        WHERE s.mode='paper' AND s.side='SELL'
        AND s.market = b.market
        AND s.timestamp > b.timestamp
    )
    ORDER BY b.timestamp ASC
""")
positions = cur.fetchall()

if positions:
    markets = ','.join([p[0] for p in positions])
    try:
        url = f'https://api.upbit.com/v1/ticker?markets={markets}'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        res = urllib.request.urlopen(req, timeout=5)
        prices = {d['market']: d['trade_price'] for d in json.loads(res.read())}
    except:
        prices = {}

    total_invest = total_pnl = 0
    for p in positions:
        market, buy_price, amount, ts = p
        current = prices.get(market, 0)
        hold_hours = (datetime.now() - datetime.fromisoformat(ts)).total_seconds() / 3600
        if current and buy_price:
            pnl_pct = (current - buy_price) / buy_price * 100
            pnl_krw = amount * pnl_pct / 100
        else:
            pnl_pct = pnl_krw = 0
        total_invest += amount
        total_pnl += pnl_krw

        # 경고 판단
        if pnl_pct <= -3.0:
            warn = '🚨 손절 지연!'
        elif pnl_pct <= -1.6:
            warn = '⚠️  손절선 근접'
        elif hold_hours >= 24:
            warn = '⏰ 장기보유 주의'
        elif pnl_pct >= 3.0:
            warn = '💰 익절 검토'
        else:
            warn = '✅ 정상'

        print(f'  {market:15s} | 매수: {buy_price:>8,.2f}원 | 현재: {current:>8,.2f}원 | '
              f'{pnl_pct:+.2f}% ({pnl_krw:+,.0f}원) | 보유 {hold_hours:.1f}h | {warn}')

    print(f'  {"합계":15s} | 투입: {total_invest:>10,.0f}원 | 미실현 손익: {total_pnl:+,.0f}원')
else:
    print('  보유 포지션 없음')

# ── 3. 오늘 거래 현황 ────────────────────────────────────
print(f'\n[오늘({today}) 거래]')
cur.execute("""
    SELECT timestamp, market, side, profit_rate, reason
    FROM trade_history
    WHERE mode='paper' AND DATE(timestamp)=?
    ORDER BY timestamp ASC
""", (today,))
today_rows = cur.fetchall()
today_buy = today_win = today_loss = 0
for r in today_rows:
    if r[2] == 'BUY':
        today_buy += 1
    else:
        pct = f'+{r[3]:.2f}%' if r[3] > 0 else f'{r[3]:.2f}%'
        mark = '✅' if r[3] > 0 else '❌'
        if r[3] > 0: today_win += 1
        else: today_loss += 1
        print(f'  {mark} {r[0][11:16]} | {r[1]:15s} | {pct:8s} | {r[4]}')
if today_buy + today_win + today_loss == 0:
    print('  아직 거래 없음')
else:
    print(f'  매수: {today_buy}건 | 매도: {today_win+today_loss}건 (✅{today_win} / ❌{today_loss})')

# ── 4. 전체 누적 성과 (04-14 이후) ──────────────────────
print('\n[누적 성과 (04-14 이후)]')
cur.execute("""
    SELECT
        SUM(CASE WHEN side='SELL' THEN 1 ELSE 0 END),
        SUM(CASE WHEN side='SELL' AND profit_rate > 0 THEN 1 ELSE 0 END),
        ROUND(AVG(CASE WHEN side='SELL' AND profit_rate > 0 THEN profit_rate END), 3),
        ROUND(AVG(CASE WHEN side='SELL' AND profit_rate < 0 THEN profit_rate END), 3),
        ROUND(SUM(CASE WHEN side='SELL' THEN (profit_rate/100.0)*amount_krw ELSE 0 END), 0)
    FROM trade_history
    WHERE mode='paper' AND timestamp >= '2026-04-14'
""")
sells, wins, avg_win, avg_loss, profit = cur.fetchone()
losses = sells - wins if sells else 0
win_rate = wins/sells*100 if sells else 0
rr = abs(avg_win/avg_loss) if avg_loss else 0
print(f'  총 매도: {sells}건 | 승률: {win_rate:.1f}% ({wins}승/{losses}패)')
print(f'  평균수익: +{avg_win}% | 평균손실: {avg_loss}%')
print(f'  수익팩터: {rr:.3f} {"✅" if rr >= 2.0 else "⚠️ 목표 2.0+"}')
print(f'  누적수익: {profit:+,.0f}원')

# ── 5. MDD ───────────────────────────────────────────────
cur.execute("""
    SELECT (profit_rate/100.0)*amount_krw
    FROM trade_history
    WHERE mode='paper' AND side='SELL' AND timestamp >= '2026-04-14'
    ORDER BY timestamp ASC
""")
cumulative = peak = max_dd = 0
for (p,) in cur.fetchall():
    cumulative += p
    if cumulative > peak: peak = cumulative
    dd = peak - cumulative
    if dd > max_dd: max_dd = dd
mdd_pct = max_dd/peak*100 if peak > 0 else 0
mdd_warn = '✅' if mdd_pct <= 15 else '⚠️  목표 15% 이하'
print(f'  MDD: {max_dd:,.0f}원 ({mdd_pct:.2f}%) {mdd_warn}')

# ── 6. 연속 손실 ─────────────────────────────────────────
cur.execute("""
    SELECT profit_rate FROM trade_history
    WHERE mode='paper' AND side='SELL' AND timestamp >= '2026-04-14'
    ORDER BY timestamp DESC LIMIT 20
""")
streak = 0
for (pr,) in cur.fetchall():
    if pr < 0: streak += 1
    else: break
if streak >= 7:
    streak_warn = '🚨 위험! 봇 중단 검토!'
elif streak >= 5:
    streak_warn = '⚠️  경고! 시장 점검!'
else:
    streak_warn = '✅ 정상'
print(f'  연속손실: {streak}회 {streak_warn}')

# ── 7. 날짜별 현황 ───────────────────────────────────────
print('\n[날짜별 현황]')
cur.execute("""
    SELECT DATE(timestamp),
           SUM(CASE WHEN side='SELL' THEN 1 ELSE 0 END),
           SUM(CASE WHEN side='SELL' AND profit_rate > 0 THEN 1 ELSE 0 END),
           ROUND(SUM(CASE WHEN side='SELL' THEN (profit_rate/100.0)*amount_krw ELSE 0 END), 0)
    FROM trade_history
    WHERE mode='paper' AND timestamp >= '2026-04-14'
    GROUP BY DATE(timestamp)
    ORDER BY DATE(timestamp)
""")
for r in cur.fetchall():
    w = r[2]/r[1]*100 if r[1] else 0
    dp = r[3] if r[3] else 0
    bar = '█' * int(w/10)
    sign = '+' if dp >= 0 else ''
    print(f'  {r[0]} | {bar:10s} {w:.0f}% | 매도 {r[1]:2d}건 | {sign}{dp:,.0f}원')

# ── 8. 최근 5건 ──────────────────────────────────────────
print('\n[최근 매도 5건]')
cur.execute("""
    SELECT timestamp, market, profit_rate, reason
    FROM trade_history
    WHERE mode='paper' AND side='SELL' AND timestamp >= '2026-04-14'
    ORDER BY timestamp DESC LIMIT 5
""")
for r in cur.fetchall():
    pct = f'+{r[2]:.2f}%' if r[2] > 0 else f'{r[2]:.2f}%'
    mark = '✅' if r[2] > 0 else '❌'
    print(f'  {mark} {r[0][5:16]} | {r[1]:15s} | {pct:8s} | {r[3]}')

print('\n' + '=' * 60)
conn.close()
