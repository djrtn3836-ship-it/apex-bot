#!/usr/bin/env python3
# fix19b.py — FX19-A: REGIME_MATRIX Bollinger/MACD TRENDING_DOWN 허용
#              FX19-B: VolumeProfile RR RECOVERY 임계값 -0.45→-0.70
import os, shutil, py_compile
from datetime import datetime

REPO  = os.path.dirname(os.path.abspath(__file__))
BUY_F = os.path.join(REPO, "core", "engine_buy.py")
ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
BAK   = os.path.join(REPO, "archive", f"fx19b_{ts}")
os.makedirs(BAK, exist_ok=True)
shutil.copy2(BUY_F, BAK)
print(f"[FX19b] 백업 완료: {BAK}")

results = []

with open(BUY_F, encoding="utf-8") as f:
    src = f.read()

# ══════════════════════════════════════════════════════════════════
# FX19-A-1: Bollinger_Squeeze TRENDING_DOWN → True
# ══════════════════════════════════════════════════════════════════
OLD_BOLL = (
    "                'Bollinger_Squeeze': {\n"
    "                    'TRENDING_UP': True,  'RANGING': True,\n"
    "                    'VOLATILE':    True,  'TRENDING_DOWN': False,\n"
    "                    'BEAR_REVERSAL': True,  'UNKNOWN': True,\n"
    "                },"
)
NEW_BOLL = (
    "                'Bollinger_Squeeze': {\n"
    "                    'TRENDING_UP': True,  'RANGING': True,\n"
    "                    'VOLATILE':    True,  'TRENDING_DOWN': True,  # [FX19-A] RECOVERY반등\n"
    "                    'BEAR_REVERSAL': True,  'UNKNOWN': True,\n"
    "                },"
)
if OLD_BOLL in src:
    src = src.replace(OLD_BOLL, NEW_BOLL, 1)
    results.append(("FX19-A-1", "✅", "Bollinger_Squeeze TRENDING_DOWN True"))
else:
    results.append(("FX19-A-1", "⚠️", "Bollinger_Squeeze 패턴 미발견"))

# ══════════════════════════════════════════════════════════════════
# FX19-A-2: MACD_Cross TRENDING_DOWN → True
# ══════════════════════════════════════════════════════════════════
OLD_MACD = (
    "                'MACD_Cross':        {\n"
    "                    'TRENDING_UP': True,  'RANGING': False,\n"
    "                    'VOLATILE':    False, 'TRENDING_DOWN': False,\n"
    "                    'BEAR_REVERSAL': False, 'UNKNOWN': True,\n"
    "                },"
)
NEW_MACD = (
    "                'MACD_Cross':        {\n"
    "                    'TRENDING_UP': True,  'RANGING': False,\n"
    "                    'VOLATILE':    False, 'TRENDING_DOWN': True,  # [FX19-A] RECOVERY반등\n"
    "                    'BEAR_REVERSAL': False, 'UNKNOWN': True,\n"
    "                },"
)
if OLD_MACD in src:
    src = src.replace(OLD_MACD, NEW_MACD, 1)
    results.append(("FX19-A-2", "✅", "MACD_Cross TRENDING_DOWN True"))
else:
    results.append(("FX19-A-2", "⚠️", "MACD_Cross 패턴 미발견"))

# ══════════════════════════════════════════════════════════════════
# FX19-B: VolumeProfile RR RECOVERY -0.45 → -0.70
# ══════════════════════════════════════════════════════════════════
OLD_RR = '                    "RECOVERY":   -0.45,  # 회복: 중간'
NEW_RR = '                    "RECOVERY":   -0.70,  # [FX19-B] 회복장 완화'
if OLD_RR in src:
    src = src.replace(OLD_RR, NEW_RR, 1)
    results.append(("FX19-B", "✅", "VolumeProfile RECOVERY RR -0.45→-0.70"))
else:
    results.append(("FX19-B", "⚠️", "VP RR RECOVERY 패턴 미발견"))

with open(BUY_F, "w", encoding="utf-8") as f:
    f.write(src)

try:
    py_compile.compile(BUY_F, doraise=True)
    results.append(("compile", "✅", "engine_buy.py 컴파일 성공"))
except py_compile.PyCompileError as e:
    results.append(("compile", "❌", str(e)))

print("\n" + "="*55)
print("FX19b 패치 결과")
print("="*55)
for tag, status, msg in results:
    print(f"  {status} {tag}: {msg}")
print(f"\n백업: {BAK}")
print("""
다음 단계:
  git add -A
  git commit -m "fix: FX19b REGIME_MATRIX TRENDING_DOWN 허용+VP RR 완화"
  git push origin main
  taskkill /F /IM python.exe /T
  python main.py --mode paper
""")
