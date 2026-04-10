import re, shutil, sys
from datetime import datetime

filepath = 'core/engine.py'
backup   = f'core/engine_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.py'
shutil.copy(filepath, backup)
print(f'✅ 백업 완료: {backup}')

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

original = content
fix_count = 0

# ============================================================
# FIX 1: strategy_scores 미정의 오류
# strategy_scores 를 참조하는 라인 → 빈 리스트로 대체
# ============================================================
patterns_fix1 = [
    # len(strategy_scores) 참조
    (r"len\(strategy_scores\)", "0"),
    # strategy_scores 변수 단독 참조 (dict key 등)
    (r"'strategies'\s*:\s*len\(strategy_scores\)", "'strategies': 0"),
    (r'"strategies"\s*:\s*len\(strategy_scores\)', '"strategies": 0'),
    # logger 에서 strategy_scores 사용
    (r"strategy_scores", "[]"),
]
for pattern, replacement in patterns_fix1:
    new_content, n = re.subn(pattern, replacement, content)
    if n > 0:
        print(f'  [FIX1] "{pattern}" → "{replacement}" ({n}회 교체)')
        content = new_content
        fix_count += n

# ============================================================
# FIX 2: signal 속성 접근 → dict.get() 방식으로 전환
# ============================================================
patterns_fix2 = [
    (r"signal\.score",                  "signal.get('confidence', 0)"),
    (r"signal\.action",                 "signal.get('action', 'N/A')"),
    (r"signal\.reasons",                "signal.get('reasons', [])"),
    (r"signal\.contributing_strategies","signal.get('contributing_strategies', [])"),
    (r"signal\.filters_passed",         "signal.get('filters_passed', [])"),
    (r"signal\.position_size",          "signal.get('position_size', 0.1)"),
    (r"signal\.entry_price",            "signal.get('entry_price', 0)"),
    (r"signal\.stop_loss",              "signal.get('stop_loss', 0)"),
    (r"signal\.take_profit",            "signal.get('take_profit', 0)"),
    (r"signal\.metadata",               "signal.get('metadata', {})"),
    (r"signal\.timestamp",              "signal.get('timestamp', None)"),
    (r"signal\.confidence",             "signal.get('confidence', 0)"),
    (r"signal\.type",                   "signal.get('type', 'N/A')"),
]
for pattern, replacement in patterns_fix2:
    new_content, n = re.subn(pattern, replacement, content)
    if n > 0:
        print(f'  [FIX2] "{pattern}" → "{replacement}" ({n}회 교체)')
        content = new_content
        fix_count += n

# ============================================================
# FIX 3: datetime - float 타입 충돌 수정
# entry_time 이 float 일 경우 datetime 으로 변환
# ============================================================
# 패턴: (datetime.now() - entry_time) 또는 (now - entry_time) 같은 연산
# entry_time 을 datetime 으로 안전하게 변환하는 헬퍼 패턴 삽입

old_pattern = r"(entry_time\s*=\s*position\.get\(['\"]entry_time['\"].*?\))"
def fix_entry_time(m):
    original_line = m.group(0)
    safe_line = (
        original_line + "\n"
        "        # [FIX3] float → datetime 변환\n"
        "        if isinstance(entry_time, float):\n"
        "            import datetime as _dt\n"
        "            entry_time = _dt.datetime.fromtimestamp(entry_time)\n"
    )
    return safe_line

new_content, n = re.subn(old_pattern, fix_entry_time, content, flags=re.DOTALL)
if n > 0:
    print(f'  [FIX3] entry_time float→datetime 변환 삽입 ({n}회)')
    content = new_content
    fix_count += n
else:
    # 대안 패턴: now - position['entry_time'] 직접 연산 부분 수정
    old2 = r"(datetime\.now\(\)\s*-\s*)(position\[.entry_time.\]|entry_time)"
    def fix_datetime_sub(m):
        return (
            "(datetime.now() - (datetime.fromtimestamp("
            + m.group(2)
            + ") if isinstance("
            + m.group(2)
            + ", float) else "
            + m.group(2)
            + "))"
        )
    new_content, n2 = re.subn(old2, fix_datetime_sub, content)
    if n2 > 0:
        print(f'  [FIX3-alt] datetime 연산 안전 처리 ({n2}회)')
        content = new_content
        fix_count += n2
    else:
        print('  [FIX3] ⚠️ 자동 패턴 미발견 → 수동 확인 필요 (아래 수동 수정 안내 참조)')

# ============================================================
# 결과 저장
# ============================================================
if content != original:
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'\n✅ 총 {fix_count}건 수정 완료 → core/engine.py 저장됨')
else:
    print('\n⚠️ 변경 사항 없음 — 패턴이 이미 수정됐거나 구조가 다를 수 있음')
    sys.exit(1)
