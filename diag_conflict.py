# _evaluate_entry_signals MTF 체크 구간 확인
print('='*60)
print('_evaluate_entry_signals MTF 체크 구간 (L950~L1020)')
print('='*60)
with open('core/engine_buy.py', encoding='utf-8') as f:
    blines = f.readlines()
for i, l in enumerate(blines[948:1025], start=949):
    print(f'L{i}: {l}', end='')

# FAST-ENTRY 블록 현재 코드 확인
print()
print('='*60)
print('FAST-ENTRY 블록 현재 코드 (L120~L165)')
print('='*60)
for i, l in enumerate(blines[119:165], start=120):
    print(f'L{i}: {l}', end='')
