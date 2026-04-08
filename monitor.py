"""
APEX BOT 종합 상태 모니터
실행: python monitor.py

수정 이력:
  v1.1 - net/10000 퍼센트 계산 오류 수정 (net / 초기자본 * 100)
       - DB 연결 try/finally 로 안전하게 닫기
       - 초기자본 settings 에서 읽도록 통일
"""
import sqlite3
from pathlib import Path
from datetime import datetime

# ── 초기자본 settings 에서 읽기 ──────────────────────────────────
try:
    from config.settings import get_settings
    _settings = get_settings()
    INITIAL_CAPITAL = getattr(
        _settings.trading, "initial_capital", 1_000_000
    )
except Exception:
    INITIAL_CAPITAL = 1_000_000  # fallback

DB_PATH = "database/apex_bot.db"

print("=" * 60)
print(f"  📊 APEX BOT 상태 보고서  [{datetime.now().strftime('%Y-%m-%d %H:%M')}]")
print("=" * 60)

# ── DB 존재 확인 ─────────────────────────────────────────────────
if not Path(DB_PATH).exists():
    print(f"\n  ❌ DB 파일 없음: {DB_PATH}")
    print("     봇을 한 번 실행하면 자동 생성됩니다.\n")
    exit(0)

# ✅ FIX: try/finally 로 DB 연결 안전하게 닫기
conn = sqlite3.connect(DB_PATH)
try:
    cur = conn.cursor()

    # ── 1. 거래 성과 ─────────────────────────────────────────────
    cur.execute("""
        SELECT
            COUNT(*),
            SUM(CASE WHEN side='SELL' AND profit_rate > 0 THEN 1 ELSE 0 END),
            SUM(CASE WHEN side='SELL' AND profit_rate <= 0 THEN 1 ELSE 0 END),
            ROUND(AVG(CASE WHEN side='SELL' THEN profit_rate END), 4),
            ROUND(MAX(CASE WHEN side='SELL' THEN profit_rate END), 4),
            ROUND(MIN(CASE WHEN side='SELL' THEN profit_rate END), 4)
        FROM trade_history
    """)
    row = cur.fetchone()

    wins        = row[1] or 0
    losses      = row[2] or 0
    total_sells = wins + losses
    win_rate    = round(wins / total_sells * 100, 1) if total_sells > 0 else 0

    # ✅ FIX: 순손익 KRW 계산 (BUY 비용 + SELL 수익 기준)
    cur.execute("""
        SELECT SUM(
            CASE WHEN side='BUY'  THEN -(amount_krw + fee)
                 WHEN side='SELL' THEN  (amount_krw - fee)
                 ELSE 0 END
        )
        FROM trade_history
    """)
    net = cur.fetchone()[0] or 0

    # ✅ FIX: 퍼센트 = 순손익 / 초기자본 * 100
    net_pct = net / INITIAL_CAPITAL * 100 if INITIAL_CAPITAL > 0 else 0

    print(f"\n  💰 거래 성과")
    print(f"     총 거래   : {row[0]}건")
    print(f"     승률      : {win_rate}%  (승 {wins} / 패 {losses})")
    # profit_rate 는 % 단위로 DB 저장 (예: 2.5 = 2.5%)
    print(f"     평균 수익 : {row[3]}%")
    print(f"     최고 수익 : {row[4]}%")
    print(f"     최대 손실 : {row[5]}%")
    print(f"     순손익    : ₩{net:+,.0f}  ({net_pct:+.2f}%)")

    # ── 2. 전략별 성과 ───────────────────────────────────────────
    print(f"\n  📈 전략별 성과")
    cur.execute("""
        SELECT
            strategy,
            SUM(CASE WHEN side='SELL' AND profit_rate > 0  THEN 1 ELSE 0 END) w,
            SUM(CASE WHEN side='SELL' AND profit_rate <= 0 THEN 1 ELSE 0 END) l,
            ROUND(AVG(CASE WHEN side='SELL' THEN profit_rate END), 3) avg_p,
            ROUND(SUM(CASE WHEN side='SELL' THEN profit_rate ELSE 0 END), 3) sum_p
        FROM trade_history
        GROUP BY strategy
        ORDER BY avg_p DESC
    """)
    for s in cur.fetchall():
        t  = (s[1] or 0) + (s[2] or 0)
        wr = round((s[1] or 0) / t * 100) if t > 0 else 0
        bar = "🟢" if (s[3] or 0) > 0 else "🔴"
        print(
            f"     {bar} {s[0]:<22} "
            f"승률{wr:>3}% | 평균{s[3]:>7}% | 누적{s[4]:>8}%"
        )

    # ── 3. 종목별 성과 ───────────────────────────────────────────
    print(f"\n  💎 종목별 성과")
    cur.execute("""
        SELECT
            market,
            COUNT(*) cnt,
            ROUND(SUM(CASE WHEN side='SELL' THEN profit_rate ELSE 0 END), 3) total_p,
            MAX(timestamp) last_trade
        FROM trade_history
        GROUP BY market
        ORDER BY total_p DESC
    """)
    for m in cur.fetchall():
        icon = "✅" if (m[2] or 0) >= 0 else "❌"
        print(
            f"     {icon} {m[0]:<12} {m[1]:>3}건 | "
            f"누적{m[2]:>8}% | 최근:{str(m[3])[:16]}"
        )

    # ── 4. 오늘 거래 ─────────────────────────────────────────────
    today = datetime.now().strftime("%Y-%m-%d")
    cur.execute(f"""
        SELECT side, market, price, profit_rate, strategy, timestamp
        FROM trade_history
        WHERE timestamp LIKE '{today}%'
        ORDER BY timestamp DESC
        LIMIT 10
    """)
    today_trades = cur.fetchall()
    print(f"\n  📅 오늘 거래 ({today}) — {len(today_trades)}건")
    if today_trades:
        for t in today_trades:
            icon   = "🟢" if t[0] == "BUY" else ("✅" if (t[3] or 0) > 0 else "❌")
            profit = f"{t[3]:+.3f}%" if t[0] == "SELL" else "진입"
            print(
                f"     {icon} {t[0]:<4} {t[1]:<12} "
                f"{profit:>8} | {t[4]} | {str(t[5])[11:16]}"
            )
    else:
        print("     오늘 거래 없음")

    # ── 5. 파일 위치 ─────────────────────────────────────────────
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

    log_files = sorted(
        Path(".").rglob("*.log"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    if log_files:
        print(f"\n  📋 최신 로그 파일:")
        for lf in log_files[:3]:
            size = lf.stat().st_size // 1024
            print(f"     📄 {lf}  ({size}KB)")
    else:
        print("\n  ⚠️  로그 파일 없음 — logs/ 폴더 확인 필요")

    print("\n" + "=" * 60)

finally:
    # ✅ FIX: 예외 발생 여부와 관계없이 항상 DB 연결 닫기
    conn.close()
