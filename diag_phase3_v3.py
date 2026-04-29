# diag_phase3_v3.py
import os

base = os.path.dirname(os.path.abspath(__file__))

# position_sizer.py 전체 출력
fpath = os.path.join(base, 'risk', 'position_sizer.py')
lines = open(fpath, 'r', encoding='utf-8').readlines()
print(f"=== risk/position_sizer.py ({len(lines)}줄) 전체 ===")
for i, line in enumerate(lines, start=1):
    print(f"L{i}: {line.rstrip()}")

# engine_buy.py L995~1060
fpath2 = os.path.join(base, 'core', 'engine_buy.py')
lines2 = open(fpath2, 'r', encoding='utf-8').readlines()
print(f"\n=== engine_buy.py L995~1060 ===")
for i, line in enumerate(lines2[994:1060], start=995):
    print(f"L{i}: {line.rstrip()}")

print(f"\n=== engine_buy.py L1195~1285 ===")
for i, line in enumerate(lines2[1194:1285], start=1195):
    print(f"L{i}: {line.rstrip()}")
