#!/usr/bin/env python3
# fix17c_manual.py  –  FX17c-2 수동 정밀 패치
# engine_buy.py 의 self.mtf_merger.analyze(_tf_data) 에 global_regime 주입

import os, re, shutil, py_compile, datetime

REPO   = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(REPO, "core", "engine_buy.py")

# ── 1) 백업 ──────────────────────────────────────────────────────────────
ts  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
bak = os.path.join(REPO, "archive", f"fx17c_manual_{ts}")
os.makedirs(bak, exist_ok=True)
shutil.copy2(TARGET, bak)
print(f"[BACKUP] {bak}")

# ── 2) 파일 읽기 ──────────────────────────────────────────────────────────
with open(TARGET, encoding="utf-8") as f:
    src = f.read()

# ── 3) FX17c-2 패치 ───────────────────────────────────────────────────────
# 대상 코드 (engine_buy.py 오프셋 ~44,500):
#     _mtf_result = self.mtf_merger.analyze(_tf_data)
#
# 교체 코드:
#   _gr17c = str(getattr(
#       getattr(self, '_global_regime', None), 'value',
#       getattr(self, '_global_regime', 'UNKNOWN') or 'UNKNOWN'
#   )).upper()
#   _mtf_result = self.mtf_merger.analyze(_tf_data, global_regime=_gr17c)

OLD = "_mtf_result = self.mtf_merger.analyze(_tf_data)"
NEW = (
    "_gr17c = str(getattr(\n"
    "                            getattr(self, '_global_regime', None), 'value',\n"
    "                            getattr(self, '_global_regime', 'UNKNOWN') or 'UNKNOWN'\n"
    "                        )).upper()  # [FX17c-2] GlobalRegime 주입\n"
    "                        _mtf_result = self.mtf_merger.analyze(\n"
    "                            _tf_data, global_regime=_gr17c\n"
    "                        )  # [FX17c-2]"
)

if OLD in src:
    src_new = src.replace(OLD, NEW, 1)
    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(src_new)
    print("✅ FX17c-2: self.mtf_merger.analyze → global_regime 주입 완료")
else:
    print("⚠️  FX17c-2: 패턴 미발견 – 아래 라인을 확인 후 수동 편집하세요:")
    # 유사 라인 탐색
    for i, line in enumerate(src.splitlines(), 1):
        if "mtf_merger" in line and "analyze" in line:
            print(f"   Line {i}: {line.rstrip()}")

# ── 4) 컴파일 검증 ────────────────────────────────────────────────────────
try:
    py_compile.compile(TARGET, doraise=True)
    print("✅ py_compile 성공")
except py_compile.PyCompileError as e:
    print(f"❌ 컴파일 오류: {e}")

print("\n=== fix17c_manual 완료 ===")
print("다음 명령을 실행하세요:")
print("  git add -A")
print('  git commit -m "fix: FX17c-2 MTFMerger GlobalRegime 수동 주입"')
print("  git push origin main")
print("  taskkill /F /IM python.exe /T")
print("  python main.py --mode paper")
