"""
fix17c.py – FX17c 패치 (2종)
  FX17c-1: signals/mtf_signal_merger.py
            MTFSignalMerger.analyze() 에 global_regime 파라미터 추가
            _merge() 에 regime 전달하여 BULL soft-fail 실제 동작
  FX17c-2: core/engine_buy.py
            MTFSignalMerger.analyze() 호출 시 _global_regime 주입

실행:
    python fix17c.py
"""

import os, shutil, py_compile
from datetime import datetime

BASE   = os.path.dirname(os.path.abspath(__file__))
STAMP  = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP = os.path.join(BASE, "archive", f"fx17c_{STAMP}")
os.makedirs(BACKUP, exist_ok=True)

FILES = {
    "merger" : os.path.join(BASE, "signals", "mtf_signal_merger.py"),
    "buy"    : os.path.join(BASE, "core",    "engine_buy.py"),
}

results = []

def backup(key):
    shutil.copy2(FILES[key], os.path.join(BACKUP, os.path.basename(FILES[key])))

def read(key):
    with open(FILES[key], "r", encoding="utf-8") as f:
        return f.read()

def write(key, code):
    with open(FILES[key], "w", encoding="utf-8") as f:
        f.write(code)

def compile_check(key):
    try:
        py_compile.compile(FILES[key], doraise=True)
        return True
    except py_compile.PyCompileError as e:
        return str(e)

def ok(s, m):   results.append(("✅", s, m))
def warn(s, m): results.append(("⚠️", s, m))
def fail(s, m): results.append(("❌", s, m))


# ══════════════════════════════════════════════
# FX17c-1: MTFSignalMerger.analyze() global_regime 파라미터 추가
# ══════════════════════════════════════════════
# 현재: def analyze(self, tf_dataframes: Dict[str, pd.DataFrame]) -> MTFResult:
# 목표: global_regime 파라미터 추가 → _merge() 에 전달
#       _merge() 내 FX16-1 블록이 self._global_regime 대신 인자 사용

C1_OLD_ANALYZE = (
    "    def analyze(self, tf_dataframes: Dict[str, pd.DataFrame]) -> MTFResult:\n"
    "        \"\"\"tf_dataframes: {\"1d\": df_daily, \"4h\": df_4h, ...}\n"
    "         df  close, ema20, ema50, ema200, rsi\"\"\""
)

C1_NEW_ANALYZE = (
    "    def analyze(\n"
    "        self,\n"
    "        tf_dataframes: Dict[str, pd.DataFrame],\n"
    "        global_regime: str = \"\",   # [FX17c-1] 엔진에서 GlobalRegime 주입\n"
    "    ) -> MTFResult:\n"
    "        \"\"\"tf_dataframes: {\"1d\": df_daily, \"4h\": df_4h, ...}\n"
    "         df  close, ema20, ema50, ema200, rsi\"\"\""
)

# _merge() 호출 시 global_regime 전달
C1_OLD_MERGE_CALL = "        return self._merge(tf_signals)"

C1_NEW_MERGE_CALL = (
    "        return self._merge(tf_signals, global_regime=global_regime)"
)

# _merge() 시그니처 수정
C1_OLD_MERGE_SIG = "    def _merge(self, signals: List[TFSignal]) -> MTFResult:"

C1_NEW_MERGE_SIG = (
    "    def _merge(\n"
    "        self,\n"
    "        signals: List[TFSignal],\n"
    "        global_regime: str = \"\",  # [FX17c-1]\n"
    "    ) -> MTFResult:"
)

# FX16-1 블록에서 self._global_regime → global_regime 인자 사용
C1_OLD_FX16 = (
    "        _regime_str = str(getattr(self, '_global_regime', '') or '').upper()\n"
    "        _is_bull_regime = any(k in _regime_str for k in ('BULL', 'TRENDING_UP', 'RECOVERY'))"
)

C1_NEW_FX16 = (
    "        # [FX17c-1] self 속성 대신 주입된 global_regime 파라미터 사용\n"
    "        _regime_str = str(global_regime or '').upper()\n"
    "        _is_bull_regime = any(k in _regime_str for k in ('BULL', 'TRENDING_UP', 'RECOVERY'))"
)

try:
    backup("merger")
    code = read("merger")
    changed = False

    for old, new, label in [
        (C1_OLD_ANALYZE,    C1_NEW_ANALYZE,    "analyze() 시그니처"),
        (C1_OLD_MERGE_CALL, C1_NEW_MERGE_CALL, "_merge() 호출"),
        (C1_OLD_MERGE_SIG,  C1_NEW_MERGE_SIG,  "_merge() 시그니처"),
        (C1_OLD_FX16,       C1_NEW_FX16,       "FX16-1 regime 참조"),
    ]:
        if old in code:
            code = code.replace(old, new)
            changed = True
            ok(f"FX17c-1-{label}", f"패턴 매칭 후 교체 완료")
        else:
            warn(f"FX17c-1-{label}", "패턴 미매칭 – 이미 패치됐거나 구조 변경됨")

    if changed:
        write("merger", code)
        r = compile_check("merger")
        if r is True:
            ok("FX17c-1-compile", "mtf_signal_merger.py 컴파일 성공")
        else:
            fail("FX17c-1-compile", f"컴파일 오류: {r}")
except Exception as e:
    fail("FX17c-1", str(e))


# ══════════════════════════════════════════════
# FX17c-2: engine_buy.py MTFSignalMerger.analyze() 호출에 global_regime 주입
# ══════════════════════════════════════════════
# 현재: mtf_result = self.mtf_merger.analyze(tf_data)
#        또는 self.mtf_signal_merger.analyze(...)
# 목표: global_regime=_gr_str 인자 추가

C2_OLD_CALL_A = "mtf_result = self.mtf_merger.analyze(tf_data)"
C2_NEW_CALL_A = (
    "# [FX17c-2] GlobalRegime 주입\n"
    "                _gr17c = str(getattr(\n"
    "                    getattr(self, '_global_regime', None), 'value',\n"
    "                    getattr(self, '_global_regime', '') or ''\n"
    "                )).upper()\n"
    "                mtf_result = self.mtf_merger.analyze(\n"
    "                    tf_data, global_regime=_gr17c\n"
    "                )"
)

C2_OLD_CALL_B = "mtf_result = self.mtf_signal_merger.analyze(tf_data)"
C2_NEW_CALL_B = (
    "# [FX17c-2] GlobalRegime 주입\n"
    "                _gr17c = str(getattr(\n"
    "                    getattr(self, '_global_regime', None), 'value',\n"
    "                    getattr(self, '_global_regime', '') or ''\n"
    "                )).upper()\n"
    "                mtf_result = self.mtf_signal_merger.analyze(\n"
    "                    tf_data, global_regime=_gr17c\n"
    "                )"
)

try:
    backup("buy")
    code = read("buy")
    changed2 = False

    for old, new, label in [
        (C2_OLD_CALL_A, C2_NEW_CALL_A, "mtf_merger.analyze"),
        (C2_OLD_CALL_B, C2_NEW_CALL_B, "mtf_signal_merger.analyze"),
    ]:
        if old in code:
            code = code.replace(old, new)
            changed2 = True
            ok(f"FX17c-2-{label}", "호출부 GlobalRegime 주입 완료")

    if not changed2:
        # 패턴 탐색: analyze( 형태 변형 탐색
        import re as _re
        pat = r'(self\.mtf(?:_signal)?_merger\.analyze\()(\s*tf_data\s*\))'
        def _replacer(m):
            return (
                m.group(1) +
                "\n                    tf_data,\n"
                "                    global_regime=str(getattr(\n"
                "                        getattr(self, '_global_regime', None), 'value',\n"
                "                        getattr(self, '_global_regime', '') or ''\n"
                "                    )).upper(),  # [FX17c-2]\n"
                "                )"
            )
        new_code, n = _re.subn(pat, _replacer, code)
        if n > 0:
            code = new_code
            changed2 = True
            ok("FX17c-2-regex", f"정규식으로 {n}곳 GlobalRegime 주입 완료")
        else:
            warn("FX17c-2", "mtf_merger.analyze 호출 패턴 미발견 – 수동 확인 필요")

    if changed2:
        write("buy", code)
        r = compile_check("buy")
        if r is True:
            ok("FX17c-2-compile", "engine_buy.py 컴파일 성공")
        else:
            fail("FX17c-2-compile", f"컴파일 오류: {r}")
except Exception as e:
    fail("FX17c-2", str(e))


# ══════════════════════════════════════════════
# 결과 출력
# ══════════════════════════════════════════════
print("\n" + "=" * 66)
print(f"  FX17c 패치 결과  |  백업: {BACKUP}")
print("=" * 66)
print(f"{'아이콘':<4} {'스텝':<28} {'내용'}")
print("-" * 66)
for icon, step, msg in results:
    print(f"{icon:<4} {step:<28} {msg}")
print("=" * 66)

fail_count = sum(1 for r in results if r[0] == "❌")
warn_count = sum(1 for r in results if r[0] == "⚠️")

if fail_count == 0 and warn_count == 0:
    print("\n✅  FX17c 전체 패치 성공 – 봇 재시작 후 검증 진행")
    exit(0)
elif fail_count == 0:
    print(f"\n⚠️  FX17c 완료 (경고 {warn_count}건) – 수동 확인 후 재시작")
    exit(0)
else:
    print(f"\n❌  FX17c 실패 {fail_count}건 – 백업 복원 후 재시도")
    exit(1)
