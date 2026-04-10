import re, shutil, sys
from datetime import datetime

filepath = 'core/engine.py'
backup = f'core/engine_backup_fix3_{datetime.now().strftime("%Y%m%d_%H%M%S")}.py'
shutil.copy(filepath, backup)
print(f'✅ 백업 완료: {backup}')

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

original = content
fix_count = 0

# FIX3-A: str 체크 뒤 float 체크 없는 경우 — datetime.datetime.fromisoformat
pattern_a = r"(if isinstance\(entry_time, str\):\n(\s+)entry_time = (datetime\.datetime)\.fromisoformat\(entry_time\))"
repl_a    = r"\1\n\2elif isinstance(entry_time, float):\n\2    entry_time = \3.fromtimestamp(entry_time)"
c, n = re.subn(pattern_a, repl_a, content)
if n:
    print(f'  [FIX3-A] datetime.datetime 패턴 {n}회 수정')
    content = c; fix_count += n

# FIX3-B: _dt_ps.datetime 패턴
pattern_b = r"(if isinstance\(_entry_time, str\):\n(\s+)_entry_time = (_dt_ps\.datetime)\.fromisoformat\(_entry_time\))"
repl_b    = r"\1\n\2elif isinstance(_entry_time, float):\n\2    _entry_time = \3.fromtimestamp(_entry_time)"
c, n = re.subn(pattern_b, repl_b, content)
if n:
    print(f'  [FIX3-B] _dt_ps 패턴 {n}회 수정')
    content = c; fix_count += n

# FIX3-C: (now - entry_time) 직접 연산
pattern_c = r"\(now\s*-\s*entry_time\)"
repl_c    = "(now - (datetime.datetime.fromtimestamp(entry_time) if isinstance(entry_time, (int,float)) else entry_time))"
c, n = re.subn(pattern_c, repl_c, content)
if n:
    print(f'  [FIX3-C] (now - entry_time) 패턴 {n}회 수정')
    content = c; fix_count += n

# FIX3-D: _ppo_dt.datetime.now() - _etime
pattern_d = r"(_ppo_dt\.datetime\.now\(\)\s*-\s*)(_etime)"
repl_d    = r"(\1(_ppo_dt.datetime.fromtimestamp(\2) if isinstance(\2,(int,float)) else \2))"
c, n = re.subn(pattern_d, repl_d, content)
if n:
    print(f'  [FIX3-D] _ppo_dt._etime 패턴 {n}회 수정')
    content = c; fix_count += n

# FIX3-E: _dt_ps.datetime.now() - _entry_time
pattern_e = r"(_dt_ps\.datetime\.now\(\)\s*-\s*)(_entry_time)"
repl_e    = r"(\1(_dt_ps.datetime.fromtimestamp(\2) if isinstance(\2,(int,float)) else \2))"
c, n = re.subn(pattern_e, repl_e, content)
if n:
    print(f'  [FIX3-E] _dt_ps._entry_time 패턴 {n}회 수정')
    content = c; fix_count += n

if content != original:
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'\n✅ 총 {fix_count}건 수정 → core/engine.py 저장')
else:
    print('\n⚠️ 패턴 미발견 — 실제 코드 라인을 출력합니다:')
    lines = original.splitlines()
    for i, line in enumerate(lines, 1):
        if 'entry_time' in line or 'fromisoformat' in line or 'fromtimestamp' in line:
            print(f'  {i:5d}: {line}')
    sys.exit(1)
