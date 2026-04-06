"""
APEX BOT 종합 상태 모니터
실행: python monitor.py
"""
import sqlite3
from pathlib import Path
from datetime import datetime

print("=" * 60)
print(f"  📊 APEX BOT 상태 보고서  [{datetime.now().strftime('%Y-%m-%d %H:%M')}]")
print("=" * 60)

# ── 1. 거래 성과 ──────────────────────────────────────────
conn = sqlite3.connect("database/apex_bot.db")
cur = conn.cursor()

cur.execute("""
    SELECT COUNT(*), 
           SUM(CASE WHEN side='SELL' AND profit_rate>0 THEN 1 ELSE 0 END),
           SUM(CASE WHEN side='SELL' AND profit_rate<=0 THEN 1 ELSE 0 END),
           ROUND(AVG(CASE WHEN side='SELL' THEN profit_rate END),4),
           ROUND(MAX(CASE WHEN side='SELL' THEN profit_rate END),4),
           ROUND(MIN(CASE WHEN side='SELL' THEN profit_rate END),4)
    FROM trade_history
""")
r = conn.execute("""SELECT COUNT(*) FROM trade_history WHERE side='SELL'""").fetchone()[0]
row = cur.fetchone()
wins = row[1] or 0
losses = row[2] or 0
total_sells = wins + losses
win_rate = round(wins / total_sells * 100, 1) if total_sells > 0 else 0

cur.execute("""
    SELECT SUM(CASE WHEN side='BUY' THEN -amount_krw-fee ELSE amount_krw-fee END)
    FROM trade_history
""")
net = cur.fetchone()[0] or 0

print(f"\n  💰 거래 성과")
print(f"     총 거래   : {row[0]}건")
print(f"     승률      : {win_rate}%  (승 {wins} / 패 {losses})")
print(f"     평균 수익 : {row[3]}%")
print(f"     최고 수익 : {row[4]}%")
print(f"     최대 손실 : {row[5]}%")
print(f"     순손익    : ₩{net:+,.0f}  ({net/10000:+.2f}%)")

# ── 2. 전략별 성과 ────────────────────────────────────────
print(f"\n  📈 전략별 성과")
cur.execute("""
    SELECT strategy,
        SUM(CASE WHEN side='SELL' AND profit_rate>0 THEN 1 ELSE 0 END) w,
        SUM(CASE WHEN side='SELL' AND profit_rate<=0 THEN 1 ELSE 0 END) l,
        ROUND(AVG(CASE WHEN side='SELL' THEN profit_rate END),3) avg_p,
        ROUND(SUM(CASE WHEN side='SELL' THEN profit_rate ELSE 0 END),3) sum_p
    FROM trade_history GROUP BY strategy ORDER BY avg_p DESC
""")
for s in cur.fetchall():
    t = (s[1] or 0) + (s[2] or 0)
    wr = round((s[1] or 0)/t*100) if t > 0 else 0
    bar = "🟢" if (s[3] or 0) > 0 else "🔴"
    print(f"     {bar} {s[0]:<22} 승률{wr:>3}% | 평균{s[3]:>7}% | 누적{s[4]:>8}%")

# ── 3. 종목별 성과 ────────────────────────────────────────
print(f"\n  💎 종목별 성과")
cur.execute("""
    SELECT market,
        COUNT(*) cnt,
        ROUND(SUM(CASE WHEN side='SELL' THEN profit_rate ELSE 0 END),3) total_p,
        MAX(timestamp) last_trade
    FROM trade_history GROUP BY market ORDER BY total_p DESC
""")
for m in cur.fetchall():
    icon = "✅" if (m[2] or 0) >= 0 else "❌"
    print(f"     {icon} {m[0]:<12} {m[1]:>3}건 | 누적{m[2]:>8}% | 최근:{m[3][:16]}")

# ── 4. 오늘 거래 ──────────────────────────────────────────
today = datetime.now().strftime("%Y-%m-%d")
cur.execute(f"""
    SELECT side, market, price, profit_rate, strategy, timestamp
    FROM trade_history
    WHERE timestamp LIKE '{today}%'
    ORDER BY timestamp DESC LIMIT 10
""")
today_trades = cur.fetchall()
print(f"\n  📅 오늘 거래 ({today}) — {len(today_trades)}건")
if today_trades:
    for t in today_trades:
        icon = "🟢" if t[0]=="BUY" else ("✅" if (t[3] or 0)>0 else "❌")
        profit = f"{t[3]:+.3f}%" if t[0]=="SELL" else "진입"
        print(f"     {icon} {t[0]:<4} {t[1]:<12} {profit:>8} | {t[4]} | {t[5][11:16]}")
else:
    print("     오늘 거래 없음")

# ── 5. 로그 파일 위치 ─────────────────────────────────────
print(f"\n  📂 파일 위치")
paths = {
    "거래 DB":   "database/apex_bot.db",
    "로그 폴더": "logs/",
    "대시보드":  "http://localhost:8888",
    "전략 설정": "config/strategy_weights.json",
    "HOLD 설정": "config/hold_coins.json",
}
for name, path in paths.items():
    if path.startswith("http"):
        print(f"     🌐 {name:<10} : {path}")
    else:
        exists = "✅" if Path(path).exists() else "❌"
        print(f"     {exists} {name:<10} : {path}")

log_files = sorted(Path(".").rglob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
if log_files:
    print(f"\n  📋 최신 로그 파일:")
    for lf in log_files[:3]:
        size = lf.stat().st_size // 1024
        print(f"     📄 {lf}  ({size}KB)")
else:
    print("\n  ⚠️  로그 파일 없음 — logs/ 폴더 확인 필요")

print("\n" + "=" * 60)
conn.close()
