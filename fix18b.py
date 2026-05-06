#!/usr/bin/env python3
# fix18b.py — FX18b: MTFMerger soft-fail 조건 최종 score 기준으로 수정

import os, re, shutil, py_compile, datetime

REPO  = os.path.dirname(os.path.abspath(__file__))
MTF_F = os.path.join(REPO, "signals", "mtf_signal_merger.py")

ts  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
bak = os.path.join(REPO, "archive", f"fx18b_{ts}")
os.makedirs(bak, exist_ok=True)
shutil.copy2(MTF_F, bak)
print(f"[BACKUP] {bak}")

with open(MTF_F, encoding="utf-8") as f:
    src = f.read()

# ── FX18b: _rsi_oversold 조건을 final score 기준으로 변경 ──────────────
# 현재: _rsi_oversold = (avg_rsi <= 40) or (score >= -0.35)  [FX18-1]
# 문제: FX16-1 블록 실행 시점의 score는 rsi_bonus/tf_bonus 미반영 중간값
# 해결: 조건을 score(중간값) 기준에서 -0.40으로 확장 + RSI ≤ 45로 완화
#       BULL 레짐에서 1h DOWN 단독 차단이 -0.23 수준이면 항상 soft-fail

OLD = '_rsi_oversold   = (avg_rsi <= 40) or (score >= -0.35)  # [FX18-1] RSI 25→40 확장 + score 근접 soft-fail'
NEW = (
    '# [FX18b] BULL 레짐 soft-fail: RSI ≤ 45 OR 합산score ≥ -0.40\n'
    '        # (1h DOWN 단독 차단 시 score ≈ -0.20~-0.28 → -0.40 이내 → 항상 통과)\n'
    '        _rsi_oversold   = (avg_rsi <= 45) or (score >= -0.40)'
)

results = []
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    with open(MTF_F, "w", encoding="utf-8") as f:
        f.write(src)
    results.append(("FX18b-1", "✅", "soft-fail RSI≤45 OR score≥-0.40 으로 확장"))
else:
    # 이전 버전 패턴도 시도
    OLD2 = '_rsi_oversold   = avg_rsi <= 25'
    OLD3 = '_rsi_oversold   = (avg_rsi <= 40) or (score >= -0.35)'
    for old_pat in [OLD2, OLD3]:
        if old_pat in src:
            src = src.replace(old_pat,
                '# [FX18b] BULL soft-fail: RSI≤45 OR score≥-0.40\n'
                '        _rsi_oversold   = (avg_rsi <= 45) or (score >= -0.40)', 1)
            with open(MTF_F, "w", encoding="utf-8") as f:
                f.write(src)
            results.append(("FX18b-1", "✅", f"패턴2 교체 완료: {old_pat[:40]}"))
            break
    else:
        # 라인 탐색
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if '_rsi_oversold' in line and 'avg_rsi' in line:
                print(f"  발견 라인 {i+1}: {line.strip()}")
        results.append(("FX18b-1", "⚠️", "_rsi_oversold 패턴 미발견 — 위 라인 확인"))

# ── allow_buy 조건 확인 및 보완 ──────────────────────────────────────────
# allow_buy = score > 0.2 and not higher_down and mid_agreement
# soft-fail 후 higher_down=False 이지만 score가 여전히 음수면 allow_buy=False
# → soft-fail 시 score를 0.25로 강제 설정

OLD_AB = 'allow_buy  = score > 0.2 and not higher_down and mid_agreement'
NEW_AB = (
    '# [FX18b-2] soft-fail 후 score 음수 보정: BULL+soft-fail 시 score=0.25 강제\n'
    '        if _is_bull_regime and _rsi_oversold and not higher_down and score <= 0.2:\n'
    '            score = 0.25\n'
    '            logger.info(\n'
    '                f"[FX18b-2] BULL soft-fail score 음수 보정 → 0.25 (allow_buy 강제 활성화)"\n'
    '            )\n'
    '        allow_buy  = score > 0.2 and not higher_down and mid_agreement'
)

if OLD_AB in src:
    # MTF_F 재로드 (위에서 수정됐을 수 있음)
    with open(MTF_F, encoding="utf-8") as f:
        src = f.read()
    if OLD_AB in src:
        src = src.replace(OLD_AB, NEW_AB, 1)
        with open(MTF_F, "w", encoding="utf-8") as f:
            f.write(src)
        results.append(("FX18b-2", "✅", "allow_buy score 음수 보정 삽입 완료"))
    else:
        results.append(("FX18b-2", "⚠️", "allow_buy 패턴 파일 재로드 후 미발견"))
else:
    with open(MTF_F, encoding="utf-8") as f:
        src2 = f.read()
    if OLD_AB in src2:
        src2 = src2.replace(OLD_AB, NEW_AB, 1)
        with open(MTF_F, "w", encoding="utf-8") as f:
            f.write(src2)
        results.append(("FX18b-2", "✅", "allow_buy score 음수 보정 삽입 완료 (재로드)"))
    else:
        lines2 = src2.splitlines()
        for i, line in enumerate(lines2):
            if 'allow_buy' in line and 'score' in line and 'higher_down' in line:
                print(f"  allow_buy 라인 {i+1}: {line.strip()}")
        results.append(("FX18b-2", "⚠️", "allow_buy 패턴 미발견 — 위 라인 수동 확인"))

# ── 컴파일 검증 ────────────────────────────────────────────────────────
try:
    py_compile.compile(MTF_F, doraise=True)
    results.append(("compile", "✅", "mtf_signal_merger.py 컴파일 성공"))
except py_compile.PyCompileError as e:
    results.append(("compile", "❌", str(e)))

print("\n=== FX18b 패치 결과 ===")
print(f"{'ID':<15} {'상태':<5} 내용")
print("-" * 65)
for rid, st, msg in results:
    print(f"{rid:<15} {st:<5} {msg}")
print(f"\n백업: {bak}")
print("\n실행:")
print("  git add -A")
print('  git commit -m "fix: FX18b MTFMerger soft-fail score 음수 보정 + RSI≤45 확장"')
print("  git push origin main")
print("  taskkill /F /IM python.exe /T")
print("  python main.py --mode paper")
