import sqlite3, json

db_path = r'C:\Users\hdw38\Desktop\달콩\bot\apex_bot\database\apex_bot.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print('=' * 60)
print('=== bot_state 전체 내용 ===')
print('=' * 60)
cursor.execute("SELECT key, value, updated_at FROM bot_state ORDER BY key")
rows = cursor.fetchall()
for r in rows:
    key, value, updated_at = r
    print(f'\n[KEY] {key}  (updated: {updated_at})')
    # JSON 파싱 시도
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            for k, v in parsed.items():
                print(f'  {k}: {v}')
        elif isinstance(parsed, list):
            for i, item in enumerate(parsed):
                print(f'  [{i}] {item}')
        else:
            print(f'  {parsed}')
    except Exception:
        print(f'  {value}')

print()
print('=' * 60)
print('=== 최근 거래 내역 (trade_history 최근 30건) ===')
print('=' * 60)
cursor.execute("""
    SELECT timestamp, market, side, price, volume, amount_krw, profit_rate, strategy, reason
    FROM trade_history
    ORDER BY id DESC
    LIMIT 30
""")
trades = cursor.fetchall()
for t in trades:
    ts, market, side, price, volume, amount_krw, profit_rate, strategy, reason = t
    pnl_str = f'{profit_rate:+.2f}%' if profit_rate is not None else 'N/A'
    print(f'{ts} | {market:12s} | {side:4s} | 가격:{price:>12,.1f} | '
          f'금액:{amount_krw:>9,.0f}₩ | PnL:{pnl_str:>7s} | {strategy} | {reason}')

print()
print('=' * 60)
print('=== daily_performance (최근 10일) ===')
print('=' * 60)
cursor.execute("""
    SELECT date, total_assets, daily_pnl, trade_count, win_count, win_rate, max_drawdown
    FROM daily_performance
    ORDER BY date DESC
    LIMIT 10
""")
perfs = cursor.fetchall()
for p in perfs:
    date, total, pnl, tc, wc, wr, mdd = p
    print(f'{date} | 총자산:{total:>10,.0f}₩ | 일손익:{pnl:>+8,.0f}₩ | '
          f'거래:{tc}건 | 승:{wc}건 | 승률:{wr:.1%} | MDD:{mdd:.2%}')

conn.close()
print('\n완료')
