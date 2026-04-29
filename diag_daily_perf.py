# diag_daily_perf.py
filepath = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot\core\engine_cycle.py"

with open(filepath, "r", encoding="utf-8") as f:
    lines = f.readlines()

# L1190 ~ L1300 출력 (daily_performance 전체 함수)
print("=== engine_cycle.py L1190~L1300 ===")
for i, line in enumerate(lines[1189:1300], start=1190):
    print(f"L{i}: {line.rstrip()}")

print()

# db_manager / save 관련 파일 탐색
import os
base = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot"
print("=== DB 저장 관련 파일 탐색 ===")
for root, dirs, files in os.walk(base):
    # __pycache__ 제외
    dirs[:] = [d for d in dirs if d != '__pycache__']
    for fname in files:
        if fname.endswith(".py"):
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                if "daily_performance" in content or "save_daily" in content:
                    print(f"  발견: {fpath}")
            except Exception:
                pass

print()

# sell_cooldown / _sq 변수 탐색
print("=== _sq / sell_cooldown / cooldown_save 관련 코드 ===")
for root, dirs, files in os.walk(base):
    dirs[:] = [d for d in dirs if d != '__pycache__']
    for fname in files:
        if fname.endswith(".py"):
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    flines = f.readlines()
                for i, line in enumerate(flines):
                    if "_sq" in line or "sell_cooldown" in line.lower() or "cooldown_save" in line.lower():
                        print(f"  {fpath} L{i+1}: {line.rstrip()}")
            except Exception:
                pass
