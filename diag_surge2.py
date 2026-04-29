
import shutil, py_compile, os, re

# ── TASK 1: surge_candidates 생성 구간 확인 ───────────────────
print('='*60)
print('TASK 1: surge_candidates 생성 구간 (engine_cycle.py L790~L835)')
print('='*60)
with open('core/engine_cycle.py', encoding='utf-8') as f:
    cyc = f.readlines()
for i, l in enumerate(cyc[788:835], start=789):
    print(f'L{i}: {l}', end='')

# ── TASK 2: engine_buy.py surge_cache score 비교 구간 ─────────
print()
print('='*60)
print('TASK 2: engine_buy.py _surge_cache score 비교 구간')
print('='*60)
with open('core/engine_buy.py', encoding='utf-8') as f:
    buy = f.readlines()
for i, l in enumerate(buy, start=1):
    if '_surge_cache' in l or 'surge_score' in l.lower() or 'surge_min' in l:
        print(f'L{i}: {l}', end='')

# ── TASK 3: macd_v2 _calc_macd / _evaluate 확인 ──────────────
print()
print('='*60)
print('TASK 3: macd_v2.py _evaluate L100~L145')
print('='*60)
with open('strategies/v2/macd_v2.py', encoding='utf-8') as f:
    mv = f.readlines()
for i, l in enumerate(mv[98:145], start=99):
    print(f'L{i}: {l}', end='')
