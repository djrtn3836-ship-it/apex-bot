# fix11b.py — FX11-1-B 보완 패치
# 대상: core/engine_cycle.py
# _load_strategies() 내 OrderBlock_SMC 인스턴스 제거
# _run_strategies() 루프에 DISABLED skip 삽입

import re, shutil, datetime, py_compile, pathlib, sys

BACKUP_DIR = pathlib.Path(
    f"archive/fx11b_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
)
TARGET = "core/engine_cycle.py"

BACKUP_DIR.mkdir(parents=True, exist_ok=True)
shutil.copy2(TARGET, BACKUP_DIR / "engine_cycle.py")
print(f"백업 완료: {BACKUP_DIR}")

results = {}

try:
    path = pathlib.Path(TARGET)
    src  = path.read_text(encoding="utf-8")

    # ── 패치 A: _load_strategies 내 OrderBlock 인스턴스 제거 ─────────────
    # [FX11b-A] OrderBlockStrategy2 import를 주석 처리
    OLD_OB_IMPORT = "from strategies.v2.order_block_v2 import OrderBlockStrategy2"
    NEW_OB_IMPORT = "# [FX11b-A] OrderBlockStrategy2 완전 비활성화 — import 제거\n# from strategies.v2.order_block_v2 import OrderBlockStrategy2"

    if OLD_OB_IMPORT in src:
        src = src.replace(OLD_OB_IMPORT, NEW_OB_IMPORT, 1)
        results["FX11b-A-import"] = "OK  OrderBlockStrategy2 import 주석 처리"
    else:
        results["FX11b-A-import"] = "SKIP  (이미 주석 처리됐거나 불일치)"

    # ── 패치 B: _load_strategies 딕셔너리에서 OrderBlock_SMC 항목 제거 ──────
    # 패턴 1: 딕셔너리 내 "OrderBlock_SMC": OrderBlockStrategy2() 형태
    OLD_OB_DICT1 = '"OrderBlock_SMC": OrderBlockStrategy2(),'
    NEW_OB_DICT1 = '# [FX11b-B] "OrderBlock_SMC": OrderBlockStrategy2(),  # 완전 비활성화'

    OLD_OB_DICT2 = "'OrderBlock_SMC': OrderBlockStrategy2(),"
    NEW_OB_DICT2 = "# [FX11b-B] 'OrderBlock_SMC': OrderBlockStrategy2(),  # 완전 비활성화"

    if OLD_OB_DICT1 in src:
        src = src.replace(OLD_OB_DICT1, NEW_OB_DICT1, 1)
        results["FX11b-B-dict"] = "OK  딕셔너리 OrderBlock_SMC 항목 주석 처리 (큰따옴표)"
    elif OLD_OB_DICT2 in src:
        src = src.replace(OLD_OB_DICT2, NEW_OB_DICT2, 1)
        results["FX11b-B-dict"] = "OK  딕셔너리 OrderBlock_SMC 항목 주석 처리 (작은따옴표)"
    else:
        # regex 패치: OrderBlockStrategy2() 포함하는 줄 전체 주석화
        src, n = re.subn(
            r'(\s*["\']OrderBlock_SMC["\']\s*:\s*OrderBlockStrategy2\(\)\s*,)',
            r'  # [FX11b-B]\1  # 완전 비활성화',
            src, count=1
        )
        results["FX11b-B-dict"] = f"OK(regex {n}건)" if n else "SKIP  (패턴 불일치 — 수동 확인 필요)"

    # ── 패치 C: _run_strategies 루프에 DISABLED skip 삽입 ─────────────────
    # engine_cycle.py 의 for name, strategy in self._strategies.items(): 루프
    DISABLED_SET = '''
    # [FX11b-C] 비활성화 전략 실행 완전 차단 (engine_cycle 경로)
    _CYCLE_DISABLED: set = {'OrderBlock_SMC', 'VolBreakout', 'VWAP_Reversion'}
'''

    OLD_CYCLE_LOOP = "        for name, strategy in self._strategies.items():"
    NEW_CYCLE_LOOP = (
        "        # [FX11b-C] 비활성화 전략 실행 차단\n"
        "        _CYCLE_DISABLED = {'OrderBlock_SMC', 'VolBreakout', 'VWAP_Reversion'}\n"
        "        for name, strategy in self._strategies.items():"
    )

    if OLD_CYCLE_LOOP in src:
        # 첫 번째 등장만 교체 (여러 루프가 있을 수 있으므로)
        src = src.replace(OLD_CYCLE_LOOP, NEW_CYCLE_LOOP, 1)
        results["FX11b-C-loop-def"] = "OK  _run_strategies 루프 앞 DISABLED 세트 삽입"
    else:
        results["FX11b-C-loop-def"] = "SKIP  (루프 시그니처 불일치)"

    # ── 패치 D: 루프 바디에 continue 조건 삽입 ──────────────────────────────
    # _run_strategies 루프 직후에 name in _CYCLE_DISABLED 체크 삽입
    OLD_LOOP_BODY = (
        "        for name, strategy in self._strategies.items():\n"
        "            # [FX11b-C] 비활성화 전략 실행 차단\n"
    )
    # 위 replace 이후의 새 시그니처 기준으로 continue 삽입
    OLD_AFTER_DISABLED = (
        "        # [FX11b-C] 비활성화 전략 실행 차단\n"
        "        _CYCLE_DISABLED = {'OrderBlock_SMC', 'VolBreakout', 'VWAP_Reversion'}\n"
        "        for name, strategy in self._strategies.items():"
    )
    NEW_AFTER_DISABLED = (
        "        # [FX11b-C] 비활성화 전략 실행 차단\n"
        "        _CYCLE_DISABLED = {'OrderBlock_SMC', 'VolBreakout', 'VWAP_Reversion'}\n"
        "        for name, strategy in self._strategies.items():\n"
        "            if name in _CYCLE_DISABLED:\n"
        "                logger.debug(f'[FX11b] {name} 비활성화 전략 skip (engine_cycle)')\n"
        "                continue"
    )

    if OLD_AFTER_DISABLED in src:
        src = src.replace(OLD_AFTER_DISABLED, NEW_AFTER_DISABLED, 1)
        results["FX11b-D-continue"] = "OK  루프 바디 continue 삽입"
    else:
        results["FX11b-D-continue"] = "SKIP  (패치C 미적용 또는 불일치)"

    # ── 패치 E: _load_strategies 리스트/배열에서 OrderBlock 항목 제거 ────────
    # 리스트 형태: [OrderBlockStrategy2(), ...] 또는 strategies.append(OrderBlockStrategy2())
    src, n_append = re.subn(
        r'(self\._strategies\.append\(\s*OrderBlockStrategy2\(\)\s*\))',
        r'# [FX11b-E] \1  # OrderBlock_SMC 완전 비활성화',
        src
    )
    results["FX11b-E-append"] = f"OK(regex {n_append}건) append 패턴" if n_append else "SKIP  (append 패턴 없음)"

    path.write_text(src, encoding="utf-8")
    py_compile.compile(TARGET, doraise=True)
    print(f"컴파일 OK  {TARGET}")
    results["FX11b-전체"] = "OK"

except Exception as e:
    print(f"FX11b 실패: {e}")
    shutil.copy2(BACKUP_DIR / "engine_cycle.py", TARGET)
    results["FX11b-전체"] = f"FAIL ({e})"

# ── 결과 출력 ────────────────────────────────────────────────────────────────
print()
print("─" * 60)
for k, v in results.items():
    icon = "✅" if v.startswith("OK") else ("⚠️ " if v.startswith("SKIP") else "❌")
    print(f"{icon}  {k:<25s} {v}")
print("─" * 60)
print(f"백업: {BACKUP_DIR}")
all_ok = all(v.startswith("OK") or v.startswith("SKIP") for v in results.values())
if all_ok:
    print("✅ FX11b 패치 성공")
else:
    print("❌ 일부 실패 — 위 로그 확인 후 수동 수정 필요")
    sys.exit(1)
