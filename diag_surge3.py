import py_compile

# _check_surge L1018 이후 결과 반환 부분 확인
print('='*60)
print('_check_surge 결과 반환 부분 (L1018~L1060)')
print('='*60)
with open('core/engine_cycle.py', encoding='utf-8') as f:
    cyc = f.readlines()

for i, l in enumerate(cyc[1017:1075], start=1018):
    print(f'L{i}: {l}', end='')

# SurgeDetector.analyze 반환값 구조 확인
print()
print('='*60)
print('SurgeDetector.analyze 반환값 구조 확인')
print('='*60)
with open('core/surge_detector.py', encoding='utf-8') as f:
    sd = f.readlines()

for i, l in enumerate(sd, start=1):
    if 'is_surge' in l or 'return {' in l or "'score'" in l or '"score"' in l:
        print(f'L{i}: {l}', end='')

# engine_cycle.py _surge_cache에 저장되는 score 값 추적
print()
print('='*60)
print('surge score 최종 저장값 확인 (L1018 근처 result 처리)')
print('='*60)
for i, l in enumerate(cyc[1015:1040], start=1016):
    print(f'L{i}: {l}', end='')
