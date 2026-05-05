import pathlib, sqlite3, datetime, subprocess, re

log_dir = pathlib.Path("logs")
db_path = pathlib.Path("database/apex_bot.db")
today   = datetime.date.today().isoformat()

# ── 로그 파일 선택 ───────────────────────────────────────────
log_files = sorted(log_dir.glob("apex_bot_*.log"))
if not log_files:
    print("❌ 로그 파일 없음"); exit()
latest_log = log_files[-1]
all_lines  = latest_log.read_text(encoding="utf-8", errors="ignore").splitlines()
print(f"\n📁 로그 파일: {latest_log.name}  (총 {len(all_lines)}줄)\n")

# ══════════════════════════════════════════════════════════════
# [1] ERROR / CRITICAL 전체
# ══════════════════════════════════════════════════════════════
print("=" * 60)
print("[1] ERROR / CRITICAL 전체")
print("=" * 60)
errors = [l for l in all_lines if any(k in l for k in ["ERROR", "CRITICAL"])]
if errors:
    for l in errors:
        print(" ", l)
else:
    print("  ✅ 없음")

# ══════════════════════════════════════════════════════════════
# [2] WARNING 전체
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("[2] WARNING 전체")
print("=" * 60)
warns = [l for l in all_lines if "WARNING" in l]
if warns:
    for l in warns:
        print(" ", l)
else:
    print("  ✅ 없음")

# ══════════════════════════════════════════════════════════════
# [3] SURGE-SCAN (급등 포착 확인)
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("[3] SURGE-SCAN 급등 포착 로그")
print("=" * 60)
surges = [l for l in all_lines if any(k in l for k in ["SURGE", "급등", "SurgeDetector", "A급", "B급"])]
if surges:
    for l in surges[-30:]:
        print(" ", l)
else:
    print("  ⚠️  SURGE 로그 없음 — WS scr 수신 전이거나 급등 종목 없음")

# ══════════════════════════════════════════════════════════════
# [4] 매수 / 매도 실행 로그 전체
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("[4] 매수/매도 실행 로그 (BUY/SELL executed)")
print("=" * 60)
trades = [l for l in all_lines if any(k in l for k in
          ["BUY executed", "SELL executed", "매수 체결", "매도 체결",
           "_execute_buy", "_execute_sell", "주문완료", "체결완료"])]
if trades:
    for l in trades:
        print(" ", l)
else:
    print("  ⚠️  체결 로그 없음")

# ══════════════════════════════════════════════════════════════
# [5] 쿨다운 관련 로그 (COOLDOWN)
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("[5] 쿨다운 로그 (COOLDOWN-BUY / COOLDOWN-SET)")
print("=" * 60)
cooldowns = [l for l in all_lines if "COOLDOWN" in l]
if cooldowns:
    for l in cooldowns[-20:]:
        print(" ", l)
else:
    print("  ✅ 쿨다운 차단 없음")

# ══════════════════════════════════════════════════════════════
# [6] WebSocket 재연결 로그
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("[6] WebSocket 재연결 로그")
print("=" * 60)
ws_logs = [l for l in all_lines if any(k in l for k in
           ["ws_reconnect", "WebSocket", "reconnect", "disconnect", "연결"])]
if ws_logs:
    for l in ws_logs[-15:]:
        print(" ", l)
else:
    print("  ✅ 재연결 없음")

# ══════════════════════════════════════════════════════════════
# [7] 메인 루프 사이클 타임 (cycle ms)
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("[7] 메인 루프 사이클 타임")
print("=" * 60)
cycles = [l for l in all_lines if any(k in l for k in
          ["메인 루프 사이클", "cycle", "CYCLE", "타임아웃", "TimeoutError"])]
if cycles:
    for l in cycles[-20:]:
        print(" ", l)
else:
    print("  ⚠️  사이클 타임 로그 없음")

# ══════════════════════════════════════════════════════════════
# [8] Fear & Greed 필터 차단 로그
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("[8] Fear & Greed 필터 로그")
print("=" * 60)
fg_logs = [l for l in all_lines if any(k in l for k in
           ["FG=", "Fear", "Greed", "공포", "탐욕", "MDD-L"])]
if fg_logs:
    for l in fg_logs[-15:]:
        print(" ", l)
else:
    print("  ⚠️  F&G 로그 없음")

# ══════════════════════════════════════════════════════════════
# [9] ML 예측 이상값 (profit -100% 등)
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("[9] ML 이상값 로그 (익절 -100% 등)")
print("=" * 60)
ml_errors = [l for l in all_lines if any(k in l for k in
             ["ML익절", "-100", "profit_rate", "ZeroDivision", "entry_price=0"])]
if ml_errors:
    for l in ml_errors[-20:]:
        print(" ", l)
else:
    print("  ✅ ML 이상값 없음")

# ══════════════════════════════════════════════════════════════
# [10] 포지션 강제청산 로그
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("[10] 강제청산 로그 (시간초과 / entry_time=None)")
print("=" * 60)
force_close = [l for l in all_lines if any(k in l for k in
               ["강제청산", "시간초과", "entry_time", "72h", "48h", "24h"])]
if force_close:
    for l in force_close[-15:]:
        print(" ", l)
else:
    print("  ✅ 강제청산 없음")

# ══════════════════════════════════════════════════════════════
# [11] DB 오늘 거래 요약
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("[11] DB 오늘 거래 요약")
print("=" * 60)
try:
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("""
        SELECT side,
               COUNT(*) cnt,
               ROUND(AVG(profit_rate), 3) avg_p,
               SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END) wins,
               SUM(CASE WHEN profit_rate < 0 THEN 1 ELSE 0 END) loss,
               ROUND(SUM(profit_rate), 3) total_p
        FROM trade_history
        WHERE DATE(timestamp) = ?
        GROUP BY side
    """, (today,)).fetchall()
    if rows:
        for r in rows:
            print(f"  {r[0]:5s} | {r[1]}건 | 승{r[3]} 패{r[4]} | "
                  f"평균 {r[2]}% | 누적 {r[5]}%")
    else:
        print("  오늘 거래 없음")
    conn.close()
except Exception as e:
    print(f"  ❌ DB 오류: {e}")

# ══════════════════════════════════════════════════════════════
# [12] 봇 프로세스 상태
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("[12] 봇 프로세스 상태 (CPU / MEM)")
print("=" * 60)
try:
    result = subprocess.run(
        ["powershell", "-Command",
         "Get-Process python -ErrorAction SilentlyContinue | "
         "Select-Object Id, CPU, @{N='MEM_MB';E={[int]($_.WorkingSet64/1MB)}} | "
         "Format-Table -AutoSize"],
        capture_output=True, text=True, timeout=10
    )
    print(result.stdout.strip() or "  ⚠️  프로세스 없음")
except Exception as e:
    print(f"  ❌ 프로세스 조회 오류: {e}")

print("\n" + "=" * 60)
print("✅ 전체 로그 확인 완료")
print("=" * 60)
