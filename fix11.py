# fix11.py — FX11-1~3 패치
# FX11-1: engine_buy.py — _run_strategies() 내 OrderBlock_SMC 인스턴스 호출 완전 차단
# FX11-2: ensemble_engine.py — dynamic weight 상한 2.5×→1.8×, 하한 0.4×→0.5×
# FX11-3: ensemble_engine.py — MACD_Cross BASE_WEIGHT 1.2→1.4 상향

import re, shutil, datetime, py_compile, pathlib, sys

BACKUP_DIR = pathlib.Path(
    f"archive/fx11_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
)
TARGETS = [
    "core/engine_buy.py",
    "strategies/v2/ensemble_engine.py",
]

# ── 백업 ────────────────────────────────────────────────────────────────────
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
for t in TARGETS:
    p = pathlib.Path(t)
    dst = BACKUP_DIR / pathlib.Path(t).parent
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(t, dst / p.name)
print(f"백업 완료: {BACKUP_DIR}")

results = {}

# ════════════════════════════════════════════════════════════════════════════
# FX11-1  engine_buy.py
#   _run_strategies() 안에서 strategy 인스턴스를 직접 호출하기 전에
#   DISABLED_STRATEGIES 세트 기준으로 완전 skip 처리
#   + _DISABLED_STRATS 필터 주석 개선 (중복 방어 명시)
# ════════════════════════════════════════════════════════════════════════════
try:
    path_buy = pathlib.Path("core/engine_buy.py")
    src_buy  = path_buy.read_text(encoding="utf-8")

    # ── 패치 A: _run_strategies 메서드 앞부분에 DISABLED skip 삽입 ──────────
    # 기존 _DISABLED_STRATS 인라인 필터가 신호 수집 *후* 에 걸러내므로
    # 전략 인스턴스 자체를 호출하지 않도록 _run_strategies 내부를 수정한다.
    OLD_RUN = '''\
    async def _run_strategies(self, market: str, df) -> list:'''

    NEW_RUN = '''\
    # [FX11-1] DISABLED_STRATEGIES 기반 실행 전 완전 skip
    _FX11_DISABLED: set = {
        'OrderBlock_SMC', 'VolBreakout', 'VWAP_Reversion',
    }

    async def _run_strategies(self, market: str, df) -> list:'''

    if OLD_RUN in src_buy:
        src_buy = src_buy.replace(OLD_RUN, NEW_RUN, 1)
        results["FX11-1-A"] = "OK  _run_strategies DISABLED 클래스 변수 삽입"
    else:
        results["FX11-1-A"] = "SKIP  (이미 적용됐거나 시그니처 불일치)"

    # ── 패치 B: _run_strategies 루프 내부에 skip 조건 삽입 ──────────────────
    # 전략 루프에서 name 이 _FX11_DISABLED 에 속하면 continue
    OLD_LOOP = '''\
        for name, strategy in self.ensemble_engine._strategies.items():'''

    NEW_LOOP = '''\
        for name, strategy in self.ensemble_engine._strategies.items():
            # [FX11-1] 비활성화 전략 실행 완전 차단
            if name in self._FX11_DISABLED:
                logger.debug(f"[FX11-1] {name} 비활성화 전략 skip")
                continue'''

    if OLD_LOOP in src_buy:
        src_buy = src_buy.replace(OLD_LOOP, NEW_LOOP, 1)
        results["FX11-1-B"] = "OK  _run_strategies 루프 내 skip 삽입"
    else:
        # 대체 시그니처 시도 (engine에 따라 다를 수 있음)
        OLD_LOOP2 = '''\
        for name, strategy in self._strategies.items():'''
        NEW_LOOP2 = '''\
        for name, strategy in self._strategies.items():
            # [FX11-1] 비활성화 전략 실행 완전 차단
            if name in self._FX11_DISABLED:
                logger.debug(f"[FX11-1] {name} 비활성화 전략 skip")
                continue'''
        if OLD_LOOP2 in src_buy:
            src_buy = src_buy.replace(OLD_LOOP2, NEW_LOOP2, 1)
            results["FX11-1-B"] = "OK  _run_strategies 루프 내 skip 삽입 (alt sig)"
        else:
            results["FX11-1-B"] = "SKIP  (루프 시그니처 불일치 — 수동 확인 필요)"

    # ── 패치 C: 기존 _DISABLED_STRATS 인라인 필터 주석 강화 ────────────────
    OLD_INLINE = '''\
            # [FX10-2] OrderBlock_SMC 완전 차단 — weight=0.0 전략 신호 제거
            _DISABLED_STRATS = {\'OrderBlock_SMC\', \'VolBreakout\', \'VWAP_Reversion\'}
            signals = [
                _s for _s in (signals or [])
                if getattr(_s, \'strategy_name\', \'\') not in _DISABLED_STRATS
            ]'''

    NEW_INLINE = '''\
            # [FX10-2][FX11-1] 이중 방어: 실행 후 남은 비활성화 전략 신호 재거
            # (_run_strategies 루프에서 이미 skip됐으나 혹시 모를 잔존 신호 제거)
            _DISABLED_STRATS = {\'OrderBlock_SMC\', \'VolBreakout\', \'VWAP_Reversion\'}
            signals = [
                _s for _s in (signals or [])
                if getattr(_s, \'strategy_name\', \'\') not in _DISABLED_STRATS
            ]'''

    if OLD_INLINE in src_buy:
        src_buy = src_buy.replace(OLD_INLINE, NEW_INLINE, 1)
        results["FX11-1-C"] = "OK  인라인 필터 주석 강화"
    else:
        results["FX11-1-C"] = "SKIP  (이미 강화됐거나 불일치)"

    path_buy.write_text(src_buy, encoding="utf-8")
    py_compile.compile("core/engine_buy.py", doraise=True)
    print("컴파일 OK  core/engine_buy.py")
    results["FX11-1"] = "OK"

except Exception as e:
    print(f"FX11-1 실패: {e}")
    shutil.copy2(
        BACKUP_DIR / "core/engine_buy.py",
        "core/engine_buy.py"
    )
    results["FX11-1"] = f"FAIL ({e})"


# ════════════════════════════════════════════════════════════════════════════
# FX11-2  ensemble_engine.py
#   dynamic weight 클램핑 상한 2.5× → 1.8×, 하한 0.4× → 0.5×
#   (두 곳: _load_recent_performance / update_result)
# ════════════════════════════════════════════════════════════════════════════
try:
    path_ens = pathlib.Path("strategies/v2/ensemble_engine.py")
    src_ens  = path_ens.read_text(encoding="utf-8")

    # ── _load_recent_performance 클램핑 ─────────────────────────────────────
    OLD_CLAMP_LOAD = (
        "                    new_w     = max(self._weights[name].base_weight * 0.4,\n"
        "                                   min(new_w, self._weights[name].base_weight * 2.5))"
    )
    NEW_CLAMP_LOAD = (
        "                    # [FX11-2] 상한 2.5→1.8, 하한 0.4→0.5 (가중치 집중 완화)\n"
        "                    new_w     = max(self._weights[name].base_weight * 0.5,\n"
        "                                   min(new_w, self._weights[name].base_weight * 1.8))"
    )
    if OLD_CLAMP_LOAD in src_ens:
        src_ens = src_ens.replace(OLD_CLAMP_LOAD, NEW_CLAMP_LOAD, 1)
        results["FX11-2-load"] = "OK  _load_recent_performance 클램핑 수정"
    else:
        # 공백 차이 대비 regex 패치
        src_ens, n = re.subn(
            r"(new_w\s*=\s*max\(self\._weights\[name\]\.base_weight\s*\*\s*)0\.4"
            r"(,\s*\n\s*min\(new_w,\s*self\._weights\[name\]\.base_weight\s*\*\s*)2\.5\)",
            r"# [FX11-2] 상한 2.5→1.8, 하한 0.4→0.5\n                    \g<1>0.5\g<2>1.8)",
            src_ens, count=1
        )
        results["FX11-2-load"] = f"OK(regex {n}건)  _load_recent_performance" if n else "SKIP  (불일치)"

    # ── update_result 클램핑 ────────────────────────────────────────────────
    OLD_CLAMP_UPD = (
        "            clamped_w  = round(\n"
        "                max(w.base_weight * 0.4, min(new_w, w.base_weight * 2.5)), 3\n"
        "            )"
    )
    NEW_CLAMP_UPD = (
        "            # [FX11-2] 상한 2.5→1.8, 하한 0.4→0.5\n"
        "            clamped_w  = round(\n"
        "                max(w.base_weight * 0.5, min(new_w, w.base_weight * 1.8)), 3\n"
        "            )"
    )
    if OLD_CLAMP_UPD in src_ens:
        src_ens = src_ens.replace(OLD_CLAMP_UPD, NEW_CLAMP_UPD, 1)
        results["FX11-2-upd"] = "OK  update_result 클램핑 수정"
    else:
        src_ens, n2 = re.subn(
            r"(max\(w\.base_weight\s*\*\s*)0\.4"
            r"(,\s*min\(new_w,\s*w\.base_weight\s*\*\s*)2\.5\)",
            r"\g<1>0.5\g<2>1.8)",
            src_ens, count=1
        )
        results["FX11-2-upd"] = f"OK(regex {n2}건)  update_result" if n2 else "SKIP  (불일치)"

    path_ens.write_text(src_ens, encoding="utf-8")
    py_compile.compile("strategies/v2/ensemble_engine.py", doraise=True)
    print("컴파일 OK  strategies/v2/ensemble_engine.py (FX11-2 중간)")
    results["FX11-2"] = "OK"

except Exception as e:
    print(f"FX11-2 실패: {e}")
    shutil.copy2(
        BACKUP_DIR / "strategies/v2/ensemble_engine.py",
        "strategies/v2/ensemble_engine.py"
    )
    results["FX11-2"] = f"FAIL ({e})"


# ════════════════════════════════════════════════════════════════════════════
# FX11-3  ensemble_engine.py
#   MACD_Cross BASE_WEIGHT 1.2 → 1.4
#   (클래스 변수 + __init__ 내 _fixed_base 두 곳 모두 수정)
# ════════════════════════════════════════════════════════════════════════════
try:
    path_ens = pathlib.Path("strategies/v2/ensemble_engine.py")
    src_ens  = path_ens.read_text(encoding="utf-8")

    # ── 클래스 변수 BASE_WEIGHTS ────────────────────────────────────────────
    OLD_BW_CLASS = '        "MACD_Cross":        1.2,'
    NEW_BW_CLASS = '        "MACD_Cross":        1.4,  # [FX11-3] 1.2→1.4 신호 다양성 확보'
    if OLD_BW_CLASS in src_ens:
        src_ens = src_ens.replace(OLD_BW_CLASS, NEW_BW_CLASS, 1)
        results["FX11-3-class"] = "OK  클래스 BASE_WEIGHTS MACD_Cross 1.2→1.4"
    else:
        results["FX11-3-class"] = "SKIP  (이미 수정됐거나 불일치)"

    # ── __init__ 내 _fixed_base ─────────────────────────────────────────────
    OLD_BW_INIT = "            'MACD_Cross':        1.2,"
    NEW_BW_INIT = "            'MACD_Cross':        1.4,  # [FX11-3] 1.2→1.4"
    if OLD_BW_INIT in src_ens:
        src_ens = src_ens.replace(OLD_BW_INIT, NEW_BW_INIT, 1)
        results["FX11-3-init"] = "OK  __init__ _fixed_base MACD_Cross 1.2→1.4"
    else:
        # 공백 변형 대비
        src_ens, n3 = re.subn(
            r"(['\"]MACD_Cross['\"]\s*:\s*)1\.2(,)",
            r"\g<1>1.4\g<2>  # [FX11-3] 1.2→1.4",
            src_ens, count=2  # 클래스+init 최대 2곳
        )
        results["FX11-3-init"] = f"OK(regex {n3}건)" if n3 else "SKIP  (불일치)"

    path_ens.write_text(src_ens, encoding="utf-8")
    py_compile.compile("strategies/v2/ensemble_engine.py", doraise=True)
    print("컴파일 OK  strategies/v2/ensemble_engine.py (FX11-3 완료)")
    results["FX11-3"] = "OK"

except Exception as e:
    print(f"FX11-3 실패: {e}")
    shutil.copy2(
        BACKUP_DIR / "strategies/v2/ensemble_engine.py",
        "strategies/v2/ensemble_engine.py"
    )
    results["FX11-3"] = f"FAIL ({e})"


# ════════════════════════════════════════════════════════════════════════════
# 결과 출력
# ════════════════════════════════════════════════════════════════════════════
print()
print("─" * 56)
for k, v in results.items():
    icon = "✅" if v.startswith("OK") else ("⚠️ " if v.startswith("SKIP") else "❌")
    print(f"{icon}  {k:<20s} {v}")
print("─" * 56)
print(f"백업: {BACKUP_DIR}")
all_ok = all(v.startswith("OK") or v.startswith("SKIP") for v in results.values())
if all_ok:
    print("✅ FX11 전체 패치 성공")
else:
    print("❌ 일부 패치 실패 — 위 로그 확인 후 수동 수정 필요")
    sys.exit(1)
