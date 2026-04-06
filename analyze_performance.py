import sqlite3

conn = sqlite3.connect("database/apex_bot.db")
cur = conn.cursor()

# 전체 거래 통계
cur.execute("""
    SELECT
        COUNT(*) as total,
        SUM(CASE WHEN side='BUY' THEN 1 ELSE 0 END) as buys,
        SUM(CASE WHEN side='SELL' THEN 1 ELSE 0 END) as sells,
        SUM(CASE WHEN side='SELL' AND profit_rate > 0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN side='SELL' AND profit_rate <= 0 THEN 1 ELSE 0 END) as losses,
        ROUND(AVG(CASE WHEN side='SELL' THEN profit_rate END), 4) as avg_profit,
        ROUND(MAX(CASE WHEN side='SELL' THEN profit_rate END), 4) as max_profit,
        ROUND(MIN(CASE WHEN side='SELL' THEN profit_rate END), 4) as max_loss,
        ROUND(SUM(CASE WHEN side='SELL' THEN profit_rate ELSE 0 END), 4) as total_profit
    FROM trade_history
""")
row = cur.fetchone()
sells = row[3] + row[4] if row[3] and row[4] else 1
win_rate = round((row[3] or 0) / sells * 100, 1)

print("=" * 55)
print("  📊 APEX BOT 페이퍼 트레이딩 성과 요약")
print("=" * 55)
print(f"  총 거래   : {row[0]}건 (매수 {row[1]}, 매도 {row[2]})")
print(f"  승률      : {win_rate}% (승 {row[3]}, 패 {row[4]})")
print(f"  평균 수익 : {row[5]}%")
print(f"  최고 수익 : {row[6]}%")
print(f"  최대 손실 : {row[7]}%")
print(f"  누적 손익 : {row[8]}%")
print()

# 전략별 성과
print("  📈 전략별 성과:")
cur.execute("""
    SELECT strategy,
        COUNT(*) as cnt,
        ROUND(AVG(CASE WHEN side='SELL' THEN profit_rate END), 3) as avg_p,
        SUM(CASE WHEN side='SELL' AND profit_rate > 0 THEN 1 ELSE 0 END) as w,
        SUM(CASE WHEN side='SELL' AND profit_rate <= 0 THEN 1 ELSE 0 END) as l
    FROM trade_history
    GROUP BY strategy
    ORDER BY avg_p DESC
""")
for r in cur.fetchall():
    total_sl = (r[3] or 0) + (r[4] or 0)
    wr = round((r[3] or 0) / total_sl * 100, 0) if total_sl > 0 else 0
    print(f"    {r[0]:<22} 거래{r[1]:>3}건 | 평균{r[2]:>7}% | 승률{wr:>4}%")

print()

# 종목별 성과
print("  💰 종목별 성과:")
cur.execute("""
    SELECT market,
        COUNT(*) as cnt,
        ROUND(SUM(CASE WHEN side='SELL' THEN profit_rate ELSE 0 END), 3) as total_p
    FROM trade_history
    GROUP BY market
    ORDER BY total_p DESC
""")
for r in cur.fetchall():
    icon = "✅" if (r[2] or 0) > 0 else "❌"
    print(f"    {icon} {r[0]:<12} 거래{r[1]:>3}건 | 누적{r[2]:>8}%")

print()

# 총 손익 계산
cur.execute("""
    SELECT
        SUM(CASE WHEN side='BUY' THEN -amount_krw - fee ELSE amount_krw - fee END)
    FROM trade_history
""")
net = cur.fetchone()[0] or 0
print(f"  💵 순손익 (수수료 포함): ₩{net:,.0f}")
print(f"  💵 초기자본 ₩1,000,000 대비: {net/1000000*100:+.2f}%")
print("=" * 55)

conn.close()
