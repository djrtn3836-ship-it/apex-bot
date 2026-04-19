import sqlite3
from datetime import date

conn = sqlite3.connect('database/apex_bot.db')
cur = conn.cursor()

print('=' * 60)
print(f'APEX BOT 검증 리포트 ({date.today()})')
print('=' * 60)

# profit_rate는 % 단위로 저장됨 (-2.47 = -2.47%)
cur.execute("""
    SELECT COUNT(*) as total,
        SUM(CASE WHEN side='SELL' THEN 1 ELSE 0 END) as sells,
        SUM(CASE WHEN side='SELL' AND profit_rate > 0 THEN 1 ELSE 0 END) as wins,
        ROUND(AVG(CASE WHEN side='SELL' AND profit_rate > 0 THEN profit_rate END), 3) as avg_win,
        ROUND(AVG(CASE WHEN side='SELL' AND profit_rate < 0 THEN profit_rate END), 3) as avg_loss,
        ROUND(SUM(CASE WHEN side='SELL' THEN profit_rate * amount_krw / 100 END), 0) as total_profit
    FROM trade_history
    WHERE mode='paper' AND DATE(timestamp) >= '2026-04-14'
""")
r = cur.fetchone()
total, sells, wins, avg_win, avg_loss, profit = r
losses = (sells or 0) - (wins or 0)
wr = wins/sells*100 if sells else 0
pf = abs((avg_win*wins)/(avg_loss*losses)) if avg_loss and losses else 0

print(f'\n[기본 성과]')
print(f'  총 거래: {total}건 (매도: {sells}건)')
print(f'  승률: {wr:.1f}% ({wins}승/{losses}패) {"OK" if wr >= 65 else "NG"} 목표 65%+')
print(f'  평균 수익: +{avg_win:.3f}% / 평균 손실: {avg_loss:.3f}%')
print(f'  수익 팩터: {pf:.3f} {"OK" if pf >= 2.0 else "NG"} 목표 2.0+')
print(f'  누적 수익: {profit:,.0f}원')

print(f'\n[전략별 성과]')
cur.execute("""
    SELECT strategy,
        COUNT(*) as cnt,
        SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END) as wins,
        ROUND(AVG(profit_rate), 3) as avg_pct,
        ROUND(SUM(profit_rate * amount_krw / 100), 0) as total_krw
    FROM trade_history
    WHERE mode='paper' AND side='SELL' AND DATE(timestamp) >= '2026-04-14'
    GROUP BY strategy ORDER BY cnt DESC LIMIT 10
""")
for row in cur.fetchall():
    wr2 = row[2]/row[1]*100 if row[1] else 0
    flag = 'OK' if wr2 >= 65 else 'NG'
    print(f'  {str(row[0])[:35]:35s} | {row[1]:3d}건 | {flag} {wr2:.0f}% | 평균 {row[3]:+.3f}% | {row[4]:+,.0f}원')

print(f'\n[시간대별 성과]')
cur.execute("""
    SELECT
        CASE
            WHEN CAST(strftime('%H', timestamp) AS INT) BETWEEN 0 AND 5 THEN '00-06시'
            WHEN CAST(strftime('%H', timestamp) AS INT) BETWEEN 6 AND 11 THEN '06-12시'
            WHEN CAST(strftime('%H', timestamp) AS INT) BETWEEN 12 AND 17 THEN '12-18시'
            ELSE '18-24시'
        END as slot,
        COUNT(*) as cnt,
        SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END) as wins,
        ROUND(AVG(profit_rate), 3) as avg_pct
    FROM trade_history
    WHERE mode='paper' AND side='SELL' AND DATE(timestamp) >= '2026-04-14'
    GROUP BY slot ORDER BY slot
""")
for row in cur.fetchall():
    wr3 = row[2]/row[1]*100 if row[1] else 0
    flag = 'OK' if wr3 >= 65 else 'NG'
    print(f'  {row[0]} | {row[1]:3d}건 | {flag} {wr3:.0f}% | 평균 {row[3]:+.3f}%')

cur.execute("""
    SELECT profit_rate * amount_krw / 100 as pnl
    FROM trade_history
    WHERE mode='paper' AND side='SELL' AND DATE(timestamp) >= '2026-04-14'
    ORDER BY timestamp
""")
rows = cur.fetchall()
if rows:
    cum = 0; peak = 0; max_dd = 0
    for (pnl,) in rows:
        cum += pnl
        if cum > peak: peak = cum
        dd = peak - cum
        if dd > max_dd: max_dd = dd
    mdd_pct = max_dd/peak*100 if peak > 0 else 0
    flag = 'OK' if mdd_pct <= 15 else 'NG'
    print(f'\n[MDD] 최대낙폭: {max_dd:,.0f}원 | {flag} {mdd_pct:.2f}% (목표 15%이하)')

cur.execute("""
    SELECT profit_rate FROM trade_history
    WHERE mode='paper' AND side='SELL' AND DATE(timestamp) >= '2026-04-14'
    ORDER BY timestamp DESC LIMIT 20
""")
streak = 0
for (pr,) in cur.fetchall():
    if pr < 0: streak += 1
    else: break
flag = 'OK' if streak < 5 else ('WARN' if streak < 7 else 'DANGER')
print(f'[연속손실] {flag} 현재 {streak}회')

print(f'\n[날짜별 현황]')
cur.execute("""
    SELECT DATE(timestamp) as dt,
        SUM(CASE WHEN side='SELL' THEN 1 ELSE 0 END) as sells,
        SUM(CASE WHEN side='SELL' AND profit_rate > 0 THEN 1 ELSE 0 END) as wins,
        ROUND(SUM(CASE WHEN side='SELL' THEN profit_rate * amount_krw / 100 END), 0) as profit
    FROM trade_history
    WHERE mode='paper' AND DATE(timestamp) >= '2026-04-14'
    GROUP BY dt ORDER BY dt
""")
for row in cur.fetchall():
    s = row[1] or 0; w = row[2] or 0
    wr5 = w/s*100 if s else 0
    p = row[3] or 0
    flag = 'OK' if wr5 >= 65 else 'NG'
    krw = f'+{p:,.0f}' if p >= 0 else f'{p:,.0f}'
    print(f'  {row[0]} | {s:3d}건 | {flag} {wr5:.0f}% | {krw}원')

conn.close()
print('\n' + '='*60)
