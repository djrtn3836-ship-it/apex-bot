# fix_circuit_breaker.py
with open('core/engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# L411~L435 (index 410~434): 올바른 버전
# L437~L457 (index 436~456): 잘못된 버전 → 삭제

# 잘못된 중복 버전 제거 (L436~L458, index 435~457)
dup_start = None
dup_end   = None

for i in range(435, 460):
    if i >= len(lines):
        break
    if 'async def _check_circuit_breaker' in lines[i] and dup_start is None:
        # L411은 이미 지났으므로 두 번째 등장 = L437
        dup_start = i
    if dup_start and i > dup_start:
        stripped = lines[i].strip()
        # 다음 메서드 또는 구분선 도달 시 종료
        if stripped.startswith('async def _main_loop') or 'async def _main_loop' in lines[i]:
            dup_end = i
            break

print(f"중복 범위: L{dup_start+1} ~ L{dup_end}")

import shutil
shutil.copy('core/engine.py', 'core/engine.py.bak_cb')

# L437~L458 구간 제거 (separator 주석 포함)
# 더 안전하게: 두 번째 _check_circuit_breaker def부터 _main_loop 직전까지 삭제
new_lines = []
skip = False
second_cb_found = False
first_cb_done   = False

for i, line in enumerate(lines):
    if 'async def _check_circuit_breaker' in line:
        if not first_cb_done:
            first_cb_done = True  # 첫 번째는 유지
            new_lines.append(line)
        else:
            skip = True  # 두 번째부터 스킵
            second_cb_found = True
            print(f"중복 제거 시작: L{i+1}")
        continue
    if skip:
        if 'async def _main_loop' in line:
            skip = False
            print(f"중복 제거 완료: L{i+1}까지")
            new_lines.append(line)
        # else: 스킵 (중복 버전 제거)
        continue
    new_lines.append(line)

if not second_cb_found:
    print("중복 버전을 찾지 못했습니다 - 수동 확인 필요")
else:
    with open('core/engine.py', 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    print(f"✅ 중복 _check_circuit_breaker 제거 완료")
    print(f"   L411 버전(올바른 settings.risk.daily_loss_limit 참조)만 유지됨")

# 검증
with open('core/engine.py', 'r', encoding='utf-8') as f:
    content = f.read()
count = content.count('async def _check_circuit_breaker')
print(f"남은 _check_circuit_breaker 정의 수: {count}개 (정상=1)")
