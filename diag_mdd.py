# diag_mdd.py
import subprocess, sys

filepath = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot\core\engine_cycle.py"

with open(filepath, "r", encoding="utf-8") as f:
    lines = f.readlines()

keywords = ["max_drawdown", "mdd", "daily_performance", "total_assets",
            "win_rate", "win_count", "daily_pnl"]

print("=== MDD / daily_performance 관련 코드 위치 ===")
for i, line in enumerate(lines):
    low = line.lower()
    if any(k in low for k in keywords):
        print(f"L{i+1}: {line.rstrip()}")

print("\n=== db_manager.py 에서도 확인 ===")
filepath2 = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot\core\db_manager.py"
try:
    with open(filepath2, "r", encoding="utf-8") as f:
        lines2 = f.readlines()
    for i, line in enumerate(lines2):
        low = line.lower()
        if any(k in low for k in keywords):
            print(f"L{i+1}: {line.rstrip()}")
except FileNotFoundError:
    print("db_manager.py 없음 - 다른 경로 확인 필요")
