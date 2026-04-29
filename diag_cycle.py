# diag_cycle.py - 48h 조건 패치
import os, shutil, py_compile

base = os.path.dirname(os.path.abspath(__file__))
fpath = os.path.join(base, 'core', 'engine_cycle.py')

# 백업
shutil.copy2(fpath, fpath + '.bak_48h')

content = open(fpath, 'r', encoding='utf-8').read()

# 기존 조건: ±1% 횡보만 청산
old = "if held_hours >= 48 and -0.01 <= profit_rate <= 0.01:"

# 수정 조건: ±3% 이내 모두 청산 (48h 이상이면 방향 불문 청산)
new = "if held_hours >= 48 and -0.03 <= profit_rate <= 0.03:"

if old in content:
    content = content.replace(old, new)
    open(fpath, 'w', encoding='utf-8').write(content)
    try:
        py_compile.compile(fpath, doraise=True)
        print('✅ 48h 청산 조건 패치 성공: ±1% → ±3%')
        print('   KRW-ADA (-1.9%) 및 KRW-AVAX (+0.2%) 모두 다음 사이클에 자동 청산됩니다')
    except py_compile.PyCompileError as e:
        shutil.copy2(fpath + '.bak_48h', fpath)
        print(f'❌ 컴파일 실패 롤백: {e}')
else:
    print('⚠️ 패턴 불일치 – 현재 조건:')
    for i, line in enumerate(open(fpath,'r',encoding='utf-8').readlines()[410:420], start=411):
        print(f'  L{i}: {line.rstrip()}')
