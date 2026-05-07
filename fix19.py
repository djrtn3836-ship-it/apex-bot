#!/usr/bin/env python3
# fix19.py — FX19-1/2/3: SignalCombiner RSI가중치 + _ml_conf 폴백 강화
import os, shutil, py_compile
from datetime import datetime

REPO       = os.path.dirname(os.path.abspath(__file__))
COMBINER_F = os.path.join(REPO, "signals", "signal_combiner.py")
BUY_F      = os.path.join(REPO, "core",    "engine_buy.py")
ts         = datetime.now().strftime("%Y%m%d_%H%M%S")
BAK_DIR    = os.path.join(REPO, "archive", f"fx19_{ts}")
os.makedirs(BAK_DIR, exist_ok=True)

results = []

# ── 백업 ──────────────────────────────────────────────────────────
shutil.copy2(COMBINER_F, BAK_DIR)
shutil.copy2(BUY_F,      BAK_DIR)
print(f"[FX19] 백업 완료: {BAK_DIR}")

# ══════════════════════════════════════════════════════════════════
# FX19-1: signal_combiner.py — RSI_Divergence 가중치 1.4 명시
# ══════════════════════════════════════════════════════════════════
OLD_WEIGHTS = (
    "        StrategyKey.BOLLINGER_SQUEEZE: 1.4,\n"
    "        StrategyKey.ATR_CHANNEL:       1.0,"
)
NEW_WEIGHTS = (
    "        StrategyKey.BOLLINGER_SQUEEZE: 1.4,\n"
    "        StrategyKey.RSI_DIVERGENCE:    1.4,   # [FX19-1] 명시적 가중치\n"
    "        StrategyKey.ATR_CHANNEL:       1.0,"
)
with open(COMBINER_F, encoding="utf-8") as f:
    src = f.read()

if OLD_WEIGHTS in src:
    src = src.replace(OLD_WEIGHTS, NEW_WEIGHTS, 1)
    with open(COMBINER_F, "w", encoding="utf-8") as f:
        f.write(src)
    results.append(("FX19-1", "✅", "RSI_Divergence weight=1.4 추가"))
else:
    results.append(("FX19-1", "⚠️", "패턴 미발견 — constants.py의 RSI_DIVERGENCE 키 확인 필요"))

# FX19-1 컴파일 체크
try:
    py_compile.compile(COMBINER_F, doraise=True)
    results.append(("FX19-1-compile", "✅", "signal_combiner.py 컴파일 성공"))
except py_compile.PyCompileError as e:
    results.append(("FX19-1-compile", "❌", str(e)))

# ══════════════════════════════════════════════════════════════════
# FX19-2/3: engine_buy.py — _ml_conf 취득 및 폴백 강화
# ══════════════════════════════════════════════════════════════════
OLD_ML_CONF = (
    "            _ml_conf = (\n"
    "                getattr(signal, \"confidence\", 0.5)\n"
    "                if _is_surge_kelly\n"
    "                else getattr(signal, \"ml_confidence\", 0.5)\n"
    "            )"
)
NEW_ML_CONF = (
    "            # [FX19-2] ml_confidence=0 시 confidence 자동 폴백\n"
    "            _ml_conf = (\n"
    "                getattr(signal, \"confidence\", 0.5)\n"
    "                if _is_surge_kelly\n"
    "                else (\n"
    "                    getattr(signal, \"ml_confidence\", 0.0)\n"
    "                    or getattr(signal, \"confidence\", 0.5)  # [FX19-2] 0-폴백\n"
    "                )\n"
    "            )\n"
    "            _ml_conf = max(0.0, float(_ml_conf))"
)

OLD_FB_COND = (
    "            # [FX18c] _ml_conf=0 시 signal.confidence 폴백 (EB-4 방지)\n"
    "            if _ml_conf <= 0.0:"
)
NEW_FB_COND = (
    "            # [FX19-3] _ml_conf 저신뢰 시 signal.confidence 폴백 (EB-4 방지)\n"
    "            if _ml_conf < 0.40:  # [FX19-3] 0→0.40 확장"
)

with open(BUY_F, encoding="utf-8") as f:
    src2 = f.read()

if OLD_ML_CONF in src2:
    src2 = src2.replace(OLD_ML_CONF, NEW_ML_CONF, 1)
    results.append(("FX19-2", "✅", "_ml_conf or-폴백 주입"))
else:
    results.append(("FX19-2", "⚠️", "OLD_ML_CONF 패턴 미발견 — 수동 확인 필요"))

if OLD_FB_COND in src2:
    src2 = src2.replace(OLD_FB_COND, NEW_FB_COND, 1)
    results.append(("FX19-3", "✅", "폴백 임계값 0 → 0.40 확장"))
else:
    results.append(("FX19-3", "⚠️", "OLD_FB_COND 패턴 미발견 — 수동 확인 필요"))

with open(BUY_F, "w", encoding="utf-8") as f:
    f.write(src2)

# FX19-2/3 컴파일 체크
try:
    py_compile.compile(BUY_F, doraise=True)
    results.append(("FX19-2/3-compile", "✅", "engine_buy.py 컴파일 성공"))
except py_compile.PyCompileError as e:
    results.append(("FX19-2/3-compile", "❌", str(e)))

# ── 결과 출력 ──────────────────────────────────────────────────────
print("\n" + "="*60)
print("FX19 패치 결과")
print("="*60)
for tag, status, msg in results:
    print(f"  {status} {tag}: {msg}")
print(f"\n백업 위치: {BAK_DIR}")
print("""
다음 단계:
  git add -A
  git commit -m "fix: FX19-1/2/3 RSI가중치+_ml_conf폴백 강화"
  git push origin main
  taskkill /F /IM python.exe /T
  python main.py --mode paper
""")
