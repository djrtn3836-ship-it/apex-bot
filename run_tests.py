# run_tests.py
# 안전한 테스트 파일만 타임아웃을 걸고 순서대로 실행
import subprocess, sys, os, time

BASE = os.path.dirname(os.path.abspath(__file__))

# 안전 등급 순서로 실행
SAFE_TESTS = [
    'tests/test_all_systems.py',
    'tests/test_backtester.py',
    'tests/test_core.py',
    'tests/test_core_stability.py',
    'tests/test_cost_stress.py',
    'tests/test_independent_verification.py',
    'tests/test_signal_diagnosis.py',
    'tests/test_strategies.py',
    'tests/test_stress.py',
    'tests/test_absolute.py',       # sqlite3 + Mock
]

RISKY_TESTS = [
    'tests/test_extended.py',
    'tests/test_monte_carlo.py',
    'tests/test_monte_carlo_v2.py',
]

SEP = '=' * 60
results = []

def run_one(test_path, timeout=60):
    """단일 테스트 파일을 타임아웃 내에 실행"""
    start = time.time()
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'pytest', test_path,
             '-v', '--tb=short', '--no-header', '-q',
             '--timeout=30'],   # pytest-timeout 있으면 사용
            capture_output=True, text=True,
            cwd=BASE, timeout=timeout
        )
        elapsed = time.time() - start
        output = result.stdout + result.stderr
        lines = output.splitlines()

        # 결과 요약 추출
        summary = next(
            (l for l in reversed(lines)
             if 'passed' in l or 'failed' in l or 'error' in l
             or 'ERROR' in l or 'no tests' in l.lower()),
            '결과 없음'
        )

        # 실패/오류 라인만 추출 (최대 15줄)
        fail_lines = [
            l for l in lines
            if 'FAILED' in l or 'ERROR' in l or 'Error' in l
            or 'assert' in l.lower()
        ][:15]

        return {
            'file': test_path,
            'returncode': result.returncode,
            'summary': summary.strip(),
            'elapsed': elapsed,
            'fail_lines': fail_lines,
            'timed_out': False,
        }
    except subprocess.TimeoutExpired:
        return {
            'file': test_path,
            'returncode': -1,
            'summary': f'⏰ 타임아웃 ({timeout}s 초과)',
            'elapsed': timeout,
            'fail_lines': [],
            'timed_out': True,
        }
    except Exception as e:
        return {
            'file': test_path,
            'returncode': -2,
            'summary': f'실행 오류: {e}',
            'elapsed': 0,
            'fail_lines': [],
            'timed_out': False,
        }

# ── 안전 테스트 실행 ─────────────────────────────────────────
print(SEP)
print('  안전 테스트 실행')
print(SEP)

for test in SAFE_TESTS:
    print(f'\n  ▶ {os.path.basename(test)} ... ', end='', flush=True)
    r = run_one(test, timeout=60)
    results.append(r)

    if r['timed_out']:
        print(f'⏰ 타임아웃')
    elif r['returncode'] == 0:
        print(f'✅ {r["summary"]} ({r["elapsed"]:.1f}s)')
    else:
        print(f'❌ {r["summary"]} ({r["elapsed"]:.1f}s)')
        for fl in r['fail_lines'][:5]:
            print(f'     {fl}')

# ── 위험 테스트 (30s 타임아웃) ──────────────────────────────
print()
print(SEP)
print('  위험 테스트 (30s 타임아웃, sqlite3 직접 접근)')
print(SEP)

for test in RISKY_TESTS:
    print(f'\n  ▶ {os.path.basename(test)} ... ', end='', flush=True)
    r = run_one(test, timeout=30)
    results.append(r)

    if r['timed_out']:
        print(f'⏰ 타임아웃 — 외부 의존성 차단 필요')
    elif r['returncode'] == 0:
        print(f'✅ {r["summary"]} ({r["elapsed"]:.1f}s)')
    else:
        print(f'❌ {r["summary"]} ({r["elapsed"]:.1f}s)')
        for fl in r['fail_lines'][:5]:
            print(f'     {fl}')

# ── 최종 요약 ────────────────────────────────────────────────
print()
print(SEP)
print('  전체 결과 요약')
print(SEP)

passed  = [r for r in results if r['returncode'] == 0]
failed  = [r for r in results if r['returncode'] not in (0, -1) and not r['timed_out']]
timeout = [r for r in results if r['timed_out']]

print(f'\n  ✅ 통과: {len(passed)}개')
print(f'  ❌ 실패: {len(failed)}개')
print(f'  ⏰ 타임아웃: {len(timeout)}개')

if failed:
    print('\n  실패 목록:')
    for r in failed:
        print(f'    {os.path.basename(r["file"])}: {r["summary"]}')
        for fl in r['fail_lines'][:3]:
            print(f'      {fl}')

if timeout:
    print('\n  타임아웃 목록:')
    for r in timeout:
        print(f'    {os.path.basename(r["file"])}')

print()
total = len(results)
ok = len(passed)
print(f'  전체 통과율: {ok}/{total} = {ok/total*100:.0f}%')
print()

if len(failed) == 0 and len(timeout) == 0:
    print('  🎉 모든 테스트 통과')
elif len(failed) > 0:
    print('  ⚠️  실패한 테스트 내용을 확인하고 수정 필요')
else:
    print('  ⚠️  타임아웃 테스트는 외부 의존성 Mock 처리 필요')
