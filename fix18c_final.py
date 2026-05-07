#!/usr/bin/env python3
# fix18c_final.py — EB-4 _ml_conf=0 시 signal.confidence 폴백 패치

import os, re, shutil, py_compile, datetime

REPO  = os.path.dirname(os.path.abspath(__file__))
BUY_F = os.path.join(REPO, "core", "engine_buy.py")

ts  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
bak = os.path.join(REPO, "archive", f"fx18c_final_{ts}")
os.makedirs(bak, exist_ok=True)
shutil.copy2(BUY_F, bak)
print(f"[BACKUP] {bak}")

with open(BUY_F, encoding="utf-8") as f:
    src = f.read()

results = []

# ── FX18c-1: position_sizer 호출 직전 _ml_conf 폴백 삽입 ─────────────────
# 현재 코드: position_size = self.position_sizer.calculate(total_capital=krw, confidence=_ml_conf, ...)
# _ml_conf 가 0.0 이면 signal.confidence 로 대체
# position_sizer.calculate 호출 바로 앞에 보정 코드 삽입

OLD_PS = "            position_size = self.position_sizer.calculate(\n                total_capital    = krw,"
NEW_PS = (
    "            # [FX18c] _ml_conf=0 시 signal.confidence 폴백 (EB-4 방지)\n"
    "            if _ml_conf <= 0.0:\n"
    "                _sig_conf = float(getattr(signal, 'confidence', 0.0))\n"
    "                if _sig_conf > 0.0:\n"
    "                    logger.info(\n"
    "                        f'[FX18c] {market} _ml_conf=0 → signal.conf={_sig_conf:.3f} 폴백'\n"
    "                    )\n"
    "                    _ml_conf = _sig_conf\n"
    "                else:\n"
    "                    logger.warning(f'[FX18c] {market} _ml_conf=0 AND signal.conf=0 → 매수 스킵')\n"
    "                    self._buying_markets.discard(market)\n"
    "                    return\n"
    "            position_size = self.position_sizer.calculate(\n"
    "                total_capital    = krw,"
)

if OLD_PS in src:
    src = src.replace(OLD_PS, NEW_PS, 1)
    with open(BUY_F, "w", encoding="utf-8") as f:
        f.write(src)
    results.append(("FX18c-1", "✅", "_ml_conf 폴백 삽입 완료"))
else:
    # 패턴 미발견 시 라인 탐색
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if "position_sizer.calculate" in line and "total_capital" in lines[i+1] if i+1 < len(lines) else False:
            print(f"  Line {i+1}: {line.rstrip()}")
            print(f"  Line {i+2}: {lines[i+1].rstrip()}")
    # 단순 패턴으로 재시도
    OLD_PS2 = "position_size = self.position_sizer.calculate("
    if OLD_PS2 in src:
        idx = src.index(OLD_PS2)
        # 해당 위치 앞에 폴백 코드 삽입 (들여쓰기 맞춤)
        # 해당 라인의 들여쓰기 추출
        line_start = src.rfind('\n', 0, idx) + 1
        indent = len(src[line_start:idx]) - len(src[line_start:idx].lstrip())
        pad = " " * indent
        fallback_code = (
            f"{pad}# [FX18c] _ml_conf=0 시 signal.confidence 폴백 (EB-4 방지)\n"
            f"{pad}if _ml_conf <= 0.0:\n"
            f"{pad}    _sig_conf = float(getattr(signal, 'confidence', 0.0))\n"
            f"{pad}    if _sig_conf > 0.0:\n"
            f"{pad}        logger.info(\n"
            f"{pad}            f'[FX18c] {{market}} _ml_conf=0 → signal.conf={{_sig_conf:.3f}} 폴백'\n"
            f"{pad}        )\n"
            f"{pad}        _ml_conf = _sig_conf\n"
            f"{pad}    else:\n"
            f"{pad}        logger.warning(f'[FX18c] {{market}} _ml_conf/signal.conf 모두 0 → 스킵')\n"
            f"{pad}        self._buying_markets.discard(market)\n"
            f"{pad}        return\n"
        )
        src = src[:idx] + fallback_code + src[idx:]
        with open(BUY_F, "w", encoding="utf-8") as f:
            f.write(src)
        results.append(("FX18c-1", "✅", "_ml_conf 폴백 삽입 완료 (패턴2)"))
    else:
        results.append(("FX18c-1", "⚠️", "패턴 미발견 — 수동 확인"))

# ── 컴파일 검증 ──────────────────────────────────────────────────────
try:
    py_compile.compile(BUY_F, doraise=True)
    results.append(("compile", "✅", "engine_buy.py 컴파일 성공"))
except py_compile.PyCompileError as e:
    results.append(("compile", "❌", str(e)))

print("\n=== FX18c_final 결과 ===")
for rid, st, msg in results:
    print(f"{rid:<15} {st}  {msg}")
print(f"\n백업: {bak}")
print("\n실행:")
print("  git add -A")
print('  git commit -m "fix: FX18c _ml_conf=0 signal.conf 폴백 / EB-4 HIVE 진입 최종 해결"')
print("  git push origin main")
print("  taskkill /F /IM python.exe /T")
print("  python main.py --mode paper")
