# fix_sc_final.py
import shutil

shutil.copy('signals/signal_combiner.py', 'signals/signal_combiner.py.bak_scfinal')

with open('signals/signal_combiner.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# ── 수정 A: L121 문법 오류 수정 (index 120) ──────────────────────────
# 현재: if ml_signal and ml_confidence > 0.50  # ... :  # ...
# 목표: if ml_signal and ml_confidence > 0.50:
for i, line in enumerate(lines):
    if 'ml_confidence > 0.50' in line and i < 130:
        indent = len(line) - len(line.lstrip())
        ind = ' ' * indent
        lines[i] = f"{ind}if ml_signal and ml_confidence > 0.50:  # ML 단독 매수 방지\n"
        print(f"✅ 수정 A: L{i+1} ml_confidence 조건문 문법 오류 수정")
        break

# ── 수정 B: L137~L138 if 본문 누락 수정 (index 136~137) ──────────────
# 현재:
#   if agreement_rate < self.min_agreement:   ← 본문 없음
#   #     return None                          ← 주석
# 목표:
#   if agreement_rate < self.min_agreement:
#       return None  # BUY 동의율 미달 → HOLD
for i, line in enumerate(lines):
    if 'if agreement_rate < self.min_agreement:' in line and i < 145:
        # 다음 라인이 주석으로 된 return None인지 확인
        next_line = lines[i+1] if i+1 < len(lines) else ''
        if '#' in next_line and 'return None' in next_line:
            indent = len(line) - len(line.lstrip())
            ind = ' ' * indent
            # 현재 라인 유지, 다음 라인(주석)을 실제 return으로 교체
            lines[i+1] = f"{ind}    return None  # BUY 동의율 미달 → HOLD\n"
            print(f"✅ 수정 B: L{i+2} return None 주석 해제")
        break

# ── 수정 C: SELL pass 교체 ────────────────────────────────────────────
for i, line in enumerate(lines):
    stripped = line.strip()
    if stripped.startswith('pass') and i > 155 and i < 180:
        indent = len(line) - len(line.lstrip())
        ind = ' ' * indent
        lines[i] = (
            f"{ind}# SELL 신호 품질 검증\n"
            f"{ind}if agreement_rate < self.min_agreement and not (\n"
            f"{ind}    ml_signal == 'SELL' and ml_confidence > 0.55\n"
            f"{ind}):\n"
            f"{ind}    return None  # SELL 동의율 미달 → HOLD\n"
        )
        print(f"✅ 수정 C: L{i+1} SELL pass → min_agreement 필터 교체")
        break

with open('signals/signal_combiner.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

# ── 문법 검증 ─────────────────────────────────────────────────────────
import py_compile, sys
try:
    py_compile.compile('signals/signal_combiner.py', doraise=True)
    print("\n✅ 문법 검증 통과 - SyntaxError 없음")
except py_compile.PyCompileError as e:
    print(f"\n🔴 문법 오류 잔존: {e}")
    sys.exit(1)

# ── 최종 내용 확인 ────────────────────────────────────────────────────
with open('signals/signal_combiner.py', 'r', encoding='utf-8') as f:
    final = f.read()

print("\n── 최종 검증 ────────────────────────────────────────")
print(f"pass 잔존:           {'있음 ⚠️' if 'pass  #' in final or '            pass' in final else '없음 ✅'}")
print(f"콜론 중복 오류:       {'있음 ⚠️' if ':  #' in final and 'ml_confidence' in final else '없음 ✅'}")
print(f"agreement_rate 필터: {'활성 ✅' if 'return None  # BUY' in final else '비활성 ⚠️'}")
print(f"SELL 필터:           {'활성 ✅' if 'return None  # SELL' in final else '비활성 ⚠️'}")
print("\n✅ fix_sc_final.py 완료")
