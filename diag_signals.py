import py_compile

# L190~L210 구간 확인 (오류 발생 위치)
print('='*60)
print('FAST-ENTRY 오류 구간 확인 (L185~L215)')
print('='*60)
with open('core/engine_buy.py', encoding='utf-8') as f:
    blines = f.readlines()
for i, l in enumerate(blines[184:215], start=185):
    print(f'L{i}: {l}', end='')
