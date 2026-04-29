# diag_phase2_v2.py
import os

base = os.path.dirname(os.path.abspath(__file__))

# ── engine_buy.py 핵심 구간 ──────────────────────────────────
fpath_buy = os.path.join(base, "core", "engine_buy.py")
lines_buy = open(fpath_buy, "r", encoding="utf-8").readlines()

print("=== engine_buy.py L130~145 (SURGE 분기 진입부) ===")
for i, line in enumerate(lines_buy[129:145], start=130):
    print(f"L{i}: {line.rstrip()}")

print("\n=== engine_buy.py L175~195 (SURGE 슬롯 체크) ===")
for i, line in enumerate(lines_buy[174:195], start=175):
    print(f"L{i}: {line.rstrip()}")

print("\n=== engine_buy.py L905~920 (global_regime + policy) ===")
for i, line in enumerate(lines_buy[904:920], start=905):
    print(f"L{i}: {line.rstrip()}")

# ── engine_cycle.py 핵심 구간 ──────────────────────────────────
fpath_cycle = os.path.join(base, "core", "engine_cycle.py")
lines_cycle = open(fpath_cycle, "r", encoding="utf-8").readlines()

print("\n=== engine_cycle.py L280~315 (targets 구성) ===")
for i, line in enumerate(lines_cycle[279:315], start=280):
    print(f"L{i}: {line.rstrip()}")

print("\n=== engine_cycle.py L810~835 (GlobalRegime 갱신 블록) ===")
for i, line in enumerate(lines_cycle[809:835], start=810):
    print(f"L{i}: {line.rstrip()}")

print("\n=== engine_cycle.py L845~880 (signal_combiner 관련) ===")
for i, line in enumerate(lines_cycle[844:880], start=845):
    print(f"L{i}: {line.rstrip()}")
