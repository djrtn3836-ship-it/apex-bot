import py_compile, shutil, re

# ════════════════════════════════════════════════════════
# TASK 1: SurgeDetector threshold_a 값 확인
# ════════════════════════════════════════════════════════
print('='*60)
print('TASK 1: SurgeDetector threshold_a / SurgeConfig 확인')
print('='*60)
with open('core/surge_detector.py', encoding='utf-8') as f:
    sd = f.readlines()

for i, l in enumerate(sd, start=1):
    if 'threshold' in l or 'SurgeConfig' in l or 'cfg' in l.lower() and 'class' in l:
        print(f'L{i}: {l}', end='')

# SurgeConfig 클래스 전체
print()
print('='*60)
print('TASK 2: SurgeConfig 클래스 정의')
print('='*60)
in_class = False
for i, l in enumerate(sd, start=1):
    if 'class SurgeConfig' in l:
        in_class = True
    if in_class:
        print(f'L{i}: {l}', end='')
        if i > 1 and in_class and l.strip() == '' and i > 5:
            pass
        if in_class and l.strip().startswith('class ') and 'SurgeConfig' not in l:
            break
        if in_class and i > 50:
            break

# score 계산 방식 확인 (L220~L265)
print()
print('='*60)
print('TASK 3: score 계산 구간 (L220~L270)')
print('='*60)
for i, l in enumerate(sd[218:270], start=219):
    print(f'L{i}: {l}', end='')
