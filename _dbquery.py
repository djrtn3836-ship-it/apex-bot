import sqlite3
from datetime import datetime

conn = sqlite3.connect('database/apex_bot.db')
cur = conn.cursor()

print('\n========== [1] 현재 오픈 포지션 ==========')
cur.execute('''
    SELECT market, entry_price, stop_loss, take_profit, strategy, entry_time, amount_krw
    FROM positions ORDER BY entry_time
''')
rows = cur.fetchall()
if rows:
    print(f"{'market':<14} {'entry':>8} {'SL':>8} {'TP':>8} {'amount':>8}  strategy")
    print('-'*75)
    for r in rows:
        sl_pct = (r[2]-r[1])/r[1]*100 if r[1] else 0
        tp_pct = (r[3]-r[1])/r[1]*100 if r[1] else 0
        print(f"{r[0]:<14} {r[1]:>8.1f} {r[2]:>8.1f}({sl_pct:+.1f}%) {r[3]:>8.1f}({tp_pct:+.1f}%) {r[6]:>8.0f}KRW  {r[4]}")
else:
    print('오픈 포지션 없음')

print('\n========== [2] 오늘+어제 체결 내역 (최근 30건) ==========')
cur.execute('''
    SELECT timestamp, market, side, price, volume, amount_krw, profit_rate, reason, strategy
    FROM trade_history
    WHERE DATE(timestamp) IN ('2026-05-05','2026-05-06')
    ORDER BY id DESC LIMIT 30
''')
rows = cur.fetchall()
if rows:
    print(f"{'timestamp':<24} {'market':<14} {'side':<6} {'price':>8} {'amount':>8} {'pnl%':>7}  reason")
    print('-'*100)
    for r in rows:
        pnl = f"{r[6]*100:+.2f}%" if r[6] is not None else '   -'
        print(f"{str(r[0]):<24} {r[1]:<14} {r[2]:<6} {r[3]:>8.1f} {r[5]:>8.0f} {pnl:>7}  {r[7]}")
else:
    print('체결 내역 없음')

print('\n========== [3] 수익 집계 ==========')
cur.execute('''
    SELECT
        COUNT(*) as total,
        SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN profit_rate < 0 THEN 1 ELSE 0 END) as losses,
        ROUND(SUM(profit_rate)*100,2) as total_pnl_pct,
        ROUND(AVG(profit_rate)*100,2) as avg_pnl_pct,
        ROUND(MAX(profit_rate)*100,2) as best,
        ROUND(MIN(profit_rate)*100,2) as worst
    FROM trade_history
    WHERE DATE(timestamp) IN ('2026-05-05','2026-05-06')
    AND side IN ('SELL','sell')
''')
r = cur.fetchone()
wr = round(r[1]/r[0]*100,1) if r[0] else 0
print(f"총체결={r[0]}건  익절={r[1]}건  손절={r[2]}건  승률={wr}%")
print(f"총PnL합={r[3]}%  평균={r[4]}%  최대익절={r[5]}%  최대손절={r[6]}%")

print('\n========== [4] 전략별 성과 ==========')
cur.execute('''
    SELECT strategy,
        COUNT(*) as cnt,
        ROUND(AVG(profit_rate)*100,2) as avg_pnl,
        ROUND(SUM(profit_rate)*100,2) as total_pnl,
        SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END) as wins
    FROM trade_history
    WHERE DATE(timestamp) IN ('2026-05-05','2026-05-06')
    AND side IN ('SELL','sell')
    GROUP BY strategy ORDER BY total_pnl DESC
''')
rows = cur.fetchall()
print(f"{'strategy':<25} {'건수':>5} {'평균PnL':>8} {'총PnL':>8} {'익절':>5}")
print('-'*60)
for r in rows:
    print(f"{str(r[0]):<25} {r[1]:>5} {str(r[2])+'%':>8} {str(r[3])+'%':>8} {r[4]:>5}")

print('\n========== [5] daily_performance 최근 7일 ==========')
cur.execute('''
    SELECT date, total_assets, daily_pnl, trade_count, win_rate, max_drawdown
    FROM daily_performance ORDER BY date DESC LIMIT 7
''')
rows = cur.fetchall()
if rows:
    print(f"{'date':<12} {'총자산':>12} {'일PnL':>10} {'거래수':>6} {'승률':>7} {'MDD':>8}")
    print('-'*65)
    for r in rows:
        print(f"{r[0]:<12} {r[1]:>12,.0f} {r[2]:>10,.0f} {r[3]:>6} {str(round(r[4]*100,1))+'%':>7} {str(round(r[5]*100,2))+'%':>8}")
else:
    print('데이터 없음')

conn.close()
