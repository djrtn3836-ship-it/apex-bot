#!/usr/bin/env python3
# fix18c.py — FX18c: EB-4 position_sizer ₩0 원인 진단 및 수정

import os, re, shutil, py_compile, datetime

REPO  = os.path.dirname(os.path.abspath(__file__))
BUY_F = os.path.join(REPO, "core", "engine_buy.py")

ts  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
bak = os.path.join(REPO, "archive", f"fx18c_{ts}")
os.makedirs(bak, exist_ok=True)
shutil.copy2(BUY_F, bak)
print(f"[BACKUP] {bak}")

with open(BUY_F, encoding="utf-8") as f:
    src = f.read()

results = []

# ── FX18c-1: position_sizer 호출 전 total_capital 디버그 로그 삽입 ──────
# EB-4 경고 직전 로그: "[EB-4] KRW-HIVE position_sizer 반환 ₩0 → 주문 스킵"
# 이 WARNING 라인 위에 total_capital 값 출력 로그 삽입
OLD_EB4 = '[EB-4] KRW-HIVE position_sizer 반환'
# 실제 코드에서 position_sizer.calculate() 호출 부분을 찾아
# total_capital 인자 확인

# position_sizer.calculate 호출 패턴 탐색
import re as _re
matches = list(_re.finditer(r'position_sizer\.calculate\(', src))
if matches:
    for m in matches:
        ctx_start = max(0, m.start() - 100)
        ctx_end   = min(len(src), m.end() + 400)
        print(f"\n[발견] offset={m.start()} 컨텍스트:")
        print(src[ctx_start:ctx_end])
        print("---")
    results.append(("FX18c-탐색", "✅", f"position_sizer.calculate 호출 {len(matches)}개 발견"))
else:
    results.append(("FX18c-탐색", "⚠️", "position_sizer.calculate 패턴 미발견"))

# ── FX18c-2: EB-4 경고 바로 전에 디버그 로그 삽입 ─────────────────────
OLD_EB4_WARN = (
    'logger.warning(\n'
    '                f"[EB-4] {market} position_sizer 반환 ₩0 → 주문 스킵"'
)
NEW_EB4_WARN = (
    '# [FX18c] EB-4 직전 total_capital 디버그\n'
    '                logger.warning(\n'
    '                    f"[FX18c-DBG] {market} total_capital={_eb4_cap:.0f} "\n'
    '                    f"conf={_eb4_conf:.3f} strat={_eb4_strat} "\n'
    '                    f"regime={getattr(getattr(self,\\"_global_regime\\",None),\\"value\\",\\"?\\")}"'
    '\n                )\n'
    '                logger.warning(\n'
    '                f"[EB-4] {market} position_sizer 반환 ₩0 → 주문 스킵"'
)

# EB-4 경고 라인 위에 변수 캡처 코드 삽입
# position_sizer.calculate() 직전 라인에 _eb4_cap 등 변수 추출
OLD_PS_CALL_PATTERN = r'(_raw_size\s*=\s*self\.position_sizer\.calculate\([^)]+\))'
match_ps = _re.search(OLD_PS_CALL_PATTERN, src, _re.DOTALL)
if match_ps:
    ps_call = match_ps.group(1)
    # total_capital 인자 추출
    cap_match = _re.search(r'total_capital\s*=\s*([^,\n]+)', ps_call)
    conf_match = _re.search(r'confidence\s*=\s*([^,\n]+)', ps_call)
    strat_match = _re.search(r'strategy\s*=\s*([^,\n]+)', ps_call)
    print(f"\n[PS호출 발견]")
    print(f"  total_capital 인자: {cap_match.group(1).strip() if cap_match else '미발견'}")
    print(f"  confidence 인자: {conf_match.group(1).strip() if conf_match else '미발견'}")
    print(f"  strategy 인자: {strat_match.group(1).strip() if strat_match else '미발견'}")

    cap_expr  = cap_match.group(1).strip()  if cap_match  else "0"
    conf_expr = conf_match.group(1).strip() if conf_match else "0"
    strat_expr = strat_match.group(1).strip() if strat_match else "'?'"

    # total_capital 인자 앞에 디버그 변수 할당 삽입
    debug_pre = (
        f"# [FX18c] EB-4 사전 진단\n"
        f"                _eb4_cap   = {cap_expr}\n"
        f"                _eb4_conf  = {conf_expr}\n"
        f"                _eb4_strat = {strat_expr}\n"
        f"                logger.info(\n"
        f"                    f'[FX18c] {{market}} PS호출 전: cap={{_eb4_cap:.0f}} '\n"
        f"                    f'conf={{_eb4_conf:.3f}} strat={{_eb4_strat}}'\n"
        f"                )\n"
        f"                "
    )
    src_new = src.replace(ps_call, debug_pre + ps_call, 1)
    if debug_pre[:20] in src_new:
        src = src_new
        with open(BUY_F, "w", encoding="utf-8") as f:
            f.write(src)
        results.append(("FX18c-2", "✅", "position_sizer 호출 전 디버그 로그 삽입 완료"))
    else:
        results.append(("FX18c-2", "⚠️", "삽입 실패 — 수동 확인"))
else:
    # 단순 패턴으로 재시도
    simple = list(_re.finditer(r'self\.position_sizer\.calculate\(', src))
    for m in simple:
        ctx = src[m.start()-200:m.end()+500]
        print(f"\n[단순발견] offset={m.start()}:\n{ctx}\n---")
    results.append(("FX18c-2", "⚠️", f"_raw_size 패턴 미발견, 단순 패턴 {len(simple)}개 출력"))

# ── 컴파일 검증 ──────────────────────────────────────────────────────
try:
    py_compile.compile(BUY_F, doraise=True)
    results.append(("compile", "✅", "engine_buy.py 컴파일 성공"))
except py_compile.PyCompileError as e:
    results.append(("compile", "❌", str(e)))

print("\n=== FX18c 결과 ===")
for rid, st, msg in results:
    print(f"{rid:<20} {st}  {msg}")
