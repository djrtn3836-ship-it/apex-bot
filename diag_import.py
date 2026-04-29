import shutil, py_compile

# CombinedSignal, SignalType 실제 임포트 경로 확인
print('='*60)
print('TASK 1: CombinedSignal 실제 위치 탐색')
print('='*60)
import os
for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in ['__pycache__', '.git', 'logs', 'database']]
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            try:
                with open(path, encoding='utf-8', errors='ignore') as fp:
                    content = fp.read()
                if 'class CombinedSignal' in content or 'class SignalType' in content:
                    print(f'  발견: {path}')
                    for i, l in enumerate(content.split('\n'), 1):
                        if 'class CombinedSignal' in l or 'class SignalType' in l:
                            print(f'    L{i}: {l}')
            except:
                pass

# engine_buy.py 상단 임포트 확인
print()
print('='*60)
print('TASK 2: engine_buy.py 상단 임포트 (L1~L30)')
print('='*60)
with open('core/engine_buy.py', encoding='utf-8') as f:
    blines = f.readlines()
for i, l in enumerate(blines[:30], start=1):
    print(f'L{i}: {l}', end='')

# FAST-ENTRY 블록 L165~L197 전체
print()
print('='*60)
print('TASK 3: FAST-ENTRY 전체 블록 (L165~L197)')
print('='*60)
for i, l in enumerate(blines[164:197], start=165):
    print(f'L{i}: {l}', end='')
