# diag_final.py
import os

base = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot"

# ── 1. config/settings.py enabled_strategies 전체
print("=" * 60)
print("=== config/settings.py L90~L130 ===")
print("=" * 60)
fpath = os.path.join(base, "config", "settings.py")
with open(fpath, "r", encoding="utf-8") as f:
    lines = f.readlines()
for i, line in enumerate(lines[88:140], start=89):
    print(f"L{i}: {line.rstrip()}")

# ── 2. engine_cycle.py _load_strategies 전체
print()
print("=" * 60)
print("=== engine_cycle.py L752~L800 ===")
print("=" * 60)
fpath2 = os.path.join(base, "core", "engine_cycle.py")
with open(fpath2, "r", encoding="utf-8") as f:
    lines2 = f.readlines()
for i, line in enumerate(lines2[750:810], start=751):
    print(f"L{i}: {line.rstrip()}")

# ── 3. engine_buy.py Vol_Breakout 차단 로직 L755~L830
print()
print("=" * 60)
print("=== engine_buy.py L755~L830 ===")
print("=" * 60)
fpath3 = os.path.join(base, "core", "engine_buy.py")
with open(fpath3, "r", encoding="utf-8") as f:
    lines3 = f.readlines()
for i, line in enumerate(lines3[754:830], start=755):
    print(f"L{i}: {line.rstrip()}")

# ── 4. surge_detector.py 스코어 계산 핵심 (L1~L80, 클래스 초기화)
print()
print("=" * 60)
print("=== surge_detector.py L1~L100 (클래스/설정) ===")
print("=" * 60)
fpath4 = os.path.join(base, "core", "surge_detector.py")
with open(fpath4, "r", encoding="utf-8") as f:
    lines4 = f.readlines()
for i, line in enumerate(lines4[:100], start=1):
    print(f"L{i}: {line.rstrip()}")

# ── 5. surge_detector.py scan/detect 함수 진입부 (market 루프)
print()
print("=" * 60)
print("=== surge_detector.py scan 함수 market 루프 위치 ===")
print("=" * 60)
for i, line in enumerate(lines4):
    if any(k in line for k in ["for market in", "async def scan", "async def detect",
                                "def _scan", "USDT", "USDC", "stab", "price_change"]):
        print(f"L{i+1}: {line.rstrip()}")
