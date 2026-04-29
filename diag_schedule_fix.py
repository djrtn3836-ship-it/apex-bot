# diag_schedule_fix.py
import os

base = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot"

# engine_schedule.py L270~L380 전체 출력
print("=" * 60)
print("=== engine_schedule.py L270~L380 ===")
print("=" * 60)
fpath = os.path.join(base, "core", "engine_schedule.py")
with open(fpath, "r", encoding="utf-8") as f:
    lines = f.readlines()
for i, line in enumerate(lines[269:380], start=270):
    print(f"L{i}: {line.rstrip()}")

print()

# engine_schedule.py L340~L375 (두 번째 save_daily_performance 호출부)
print("=" * 60)
print("=== engine_schedule.py L330~L375 ===")
print("=" * 60)
for i, line in enumerate(lines[329:375], start=330):
    print(f"L{i}: {line.rstrip()}")
