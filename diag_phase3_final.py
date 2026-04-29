# diag_phase3_final.py
import os

base = os.path.dirname(os.path.abspath(__file__))
fpath = os.path.join(base, "risk", "position_sizer.py")
lines = open(fpath, "r", encoding="utf-8").readlines()

print(f"=== risk/position_sizer.py 전체 ({len(lines)}줄) ===")
for i, line in enumerate(lines, start=1):
    print(f"L{i}: {line.rstrip()}")
