# diag_bugs.py
import os

base = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot"

# ── 1. engine_db.py 전체 _save_cooldown_to_db 함수
print("=" * 60)
print("=== engine_db.py: _save_cooldown_to_db / _load_cooldown_from_db ===")
print("=" * 60)
fpath = os.path.join(base, "core", "engine_db.py")
with open(fpath, "r", encoding="utf-8") as f:
    lines = f.readlines()
for i, line in enumerate(lines[270:340], start=271):
    print(f"L{i}: {line.rstrip()}")

# ── 2. engine_schedule.py: daily_performance 저장 부분
print()
print("=" * 60)
print("=== engine_schedule.py: daily_performance 저장 관련 ===")
print("=" * 60)
fpath2 = os.path.join(base, "core", "engine_schedule.py")
with open(fpath2, "r", encoding="utf-8") as f:
    lines2 = f.readlines()
for i, line in enumerate(lines2):
    low = line.lower()
    if any(k in low for k in ["daily_performance", "save_daily", "total_assets",
                               "daily_pnl", "win_count", "trade_count"]):
        print(f"L{i+1}: {line.rstrip()}")

# ── 3. data/storage/db_manager.py: save_daily 함수
print()
print("=" * 60)
print("=== db_manager.py: save_daily / daily_performance 함수 ===")
print("=" * 60)
fpath3 = os.path.join(base, "data", "storage", "db_manager.py")
with open(fpath3, "r", encoding="utf-8") as f:
    lines3 = f.readlines()

# daily_performance 관련 줄 전체 출력
in_func = False
func_start = 0
for i, line in enumerate(lines3):
    if "daily_performance" in line or "save_daily" in line:
        # 함수 시작점 추적
        start = max(0, i - 2)
        end = min(len(lines3), i + 40)
        print(f"\n--- 발견위치 L{i+1} 주변 ---")
        for j, l in enumerate(lines3[start:end], start=start+1):
            print(f"L{j}: {l.rstrip()}")
        break

# ── 4. apex_daily.py 확인
print()
print("=" * 60)
print("=== apex_daily.py 전체 ===")
print("=" * 60)
fpath4 = os.path.join(base, "apex_daily.py")
with open(fpath4, "r", encoding="utf-8") as f:
    lines4 = f.readlines()
for i, line in enumerate(lines4):
    print(f"L{i+1}: {line.rstrip()}")
