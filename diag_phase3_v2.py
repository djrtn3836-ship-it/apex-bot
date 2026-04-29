# diag_phase3_v2.py
import os

base = os.path.dirname(os.path.abspath(__file__))

# position_sizer 파일 찾기
for root, dirs, files in os.walk(base):
    for f in files:
        if 'sizer' in f.lower() or 'position' in f.lower() and f.endswith('.py'):
            fpath = os.path.join(root, f)
            rel = os.path.relpath(fpath, base)
            lines = open(fpath, 'r', encoding='utf-8').readlines()
            print(f"\n=== {rel} ({len(lines)}줄) ===")
            for i, line in enumerate(lines, start=1):
                if any(k in line for k in [
                    'calculate', 'kelly', 'position_size',
                    'amount', 'krw', 'capital', 'fraction',
                    'win_rate', 'avg_win', 'avg_loss', 'strategy'
                ]):
                    print(f"L{i}: {line.rstrip()}")

# engine_utils.py 확인
fpath2 = os.path.join(base, 'core', 'engine_utils.py')
if os.path.exists(fpath2):
    lines2 = open(fpath2, 'r', encoding='utf-8').readlines()
    print(f"\n=== core/engine_utils.py ({len(lines2)}줄) ===")
    for i, line in enumerate(lines2, start=1):
        if any(k in line for k in [
            'calc_position_size', 'kelly', 'floor',
            'amount', 'capital', 'fraction', 'size'
        ]):
            print(f"L{i}: {line.rstrip()}")

# engine_buy.py L1000~1030 상세 확인
fpath3 = os.path.join(base, 'core', 'engine_buy.py')
lines3 = open(fpath3, 'r', encoding='utf-8').readlines()
print(f"\n=== engine_buy.py L995~1060 (Kelly + position_sizer) ===")
for i, line in enumerate(lines3[994:1060], start=995):
    print(f"L{i}: {line.rstrip()}")

print(f"\n=== engine_buy.py L1195~1280 (position_size 최종 결정) ===")
for i, line in enumerate(lines3[1194:1280], start=1195):
    print(f"L{i}: {line.rstrip()}")
