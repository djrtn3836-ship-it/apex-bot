# fix11c.py — FX11b 수동 보완 패치
# _load_strategies() 내 OrderBlockStrategy() 호출 완전 제거

import shutil, datetime, py_compile, pathlib, sys

BACKUP_DIR = pathlib.Path(
    f"archive/fx11c_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
)
TARGET = "core/engine_cycle.py"

BACKUP_DIR.mkdir(parents=True, exist_ok=True)
shutil.copy2(TARGET, BACKUP_DIR / "engine_cycle.py")
print(f"백업 완료: {BACKUP_DIR}")

results = {}

try:
    path = pathlib.Path(TARGET)
    src  = path.read_text(encoding="utf-8")

    # ── 패치 A: strategies 리스트에서 OrderBlockStrategy() 제거 ────────────
    # 현재 코드:
    #   ATRChannelStrategy(), OrderBlockStrategy(),
    # 목표:
    #   ATRChannelStrategy(),
    #   # [FX11c] OrderBlockStrategy() 완전 제거 — 비활성화(6건 전패)

    OLD_LIST = (
        "            ATRChannelStrategy(), OrderBlockStrategy(),\n"
    )
    NEW_LIST = (
        "            ATRChannelStrategy(),\n"
        "            # [FX11c] OrderBlockStrategy() 완전 제거 — 비활성화(6건 전패)\n"
    )

    if OLD_LIST in src:
        src = src.replace(OLD_LIST, NEW_LIST, 1)
        results["FX11c-A"] = "OK  strategies 리스트에서 OrderBlockStrategy() 제거"
    else:
        # 공백 변형 대비 — 한 줄에 있는 경우
        import re
        src, n = re.subn(
            r'(ATRChannelStrategy\(\)\s*,\s*)OrderBlockStrategy\(\)\s*,',
            r'\1# [FX11c] OrderBlockStrategy() 제거',
            src, count=1
        )
        if n:
            results["FX11c-A"] = f"OK(regex {n}건)  OrderBlockStrategy() 제거"
        else:
            # 다른 줄에 단독으로 있는 경우
            src, n2 = re.subn(
                r'(\s*)OrderBlockStrategy\(\)\s*,\s*\n',
                r'\1# [FX11c] OrderBlockStrategy() 완전 제거\n',
                src, count=1
            )
            results["FX11c-A"] = f"OK(regex2 {n2}건)" if n2 else "SKIP  (패턴 불일치)"

    # ── 패치 B: OrderBlockStrategy 관련 구버전 import 제거 ─────────────────
    # v1 경로 import가 남아있을 수 있음
    import re as _re
    src, nb = _re.subn(
        r'from strategies\.\w+\.\w+ import OrderBlockStrategy\b[^\n]*\n',
        '# [FX11c-B] OrderBlockStrategy v1 import 제거\n',
        src
    )
    results["FX11c-B"] = f"OK(regex {nb}건)  v1 import 제거" if nb else "SKIP  (v1 import 없음)"

    path.write_text(src, encoding="utf-8")
    py_compile.compile(TARGET, doraise=True)
    print(f"컴파일 OK  {TARGET}")
    results["FX11c-전체"] = "OK"

except Exception as e:
    print(f"FX11c 실패: {e}")
    shutil.copy2(BACKUP_DIR / "engine_cycle.py", TARGET)
    results["FX11c-전체"] = f"FAIL ({e})"

# ── 결과 출력 ────────────────────────────────────────────────────────────────
print()
print("─" * 60)
for k, v in results.items():
    icon = "✅" if v.startswith("OK") else ("⚠️ " if v.startswith("SKIP") else "❌")
    print(f"{icon}  {k:<20s} {v}")
print("─" * 60)
print(f"백업: {BACKUP_DIR}")
all_ok = all(v.startswith("OK") or v.startswith("SKIP") for v in results.values())
if all_ok:
    print("✅ FX11c 패치 성공")
else:
    print("❌ 패치 실패 — 위 로그 확인")
    sys.exit(1)
