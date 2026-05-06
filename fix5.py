#!/usr/bin/env python3
# fix5.py – FX5-1 교체매매 score 임계 0.80→0.50 | FX5-2 DOOD/OP TTL 연장 | FX5-3 네트워크 재시도 강화
import os, shutil, datetime, py_compile, re

BASE = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot"
TS   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
ARC  = os.path.join(BASE, "archive", f"fx5_{TS}")
os.makedirs(ARC, exist_ok=True)
RES  = {"OK": [], "SKIP": [], "FAIL": []}

def bk(rel):
    src = os.path.join(BASE, rel)
    dst = os.path.join(ARC, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return src

def pt_regex(rel, pattern, replacement, label):
    fp = os.path.join(BASE, rel)
    if not os.path.exists(fp):
        print(f"  [SKIP] {label}: 파일 없음")
        RES["SKIP"].append(label); return
    src = open(fp, encoding="utf-8").read()
    new, n = re.subn(pattern, replacement, src)
    if n == 0:
        print(f"  [SKIP] {label}: 패턴 미발견")
        RES["SKIP"].append(label); return
    bk(rel)
    open(fp, "w", encoding="utf-8").write(new)
    try:
        py_compile.compile(fp, doraise=True)
        print(f"  [OK]   {label} (치환 {n}건)")
        RES["OK"].append(label)
    except py_compile.PyCompileError as e:
        open(fp, "w", encoding="utf-8").write(src)
        print(f"  [FAIL] {label}: {e}")
        RES["FAIL"].append(label)

# ──────────────────────────────────────────────────────────────
# FX5-1: 교체매매 score 임계 0.80 → 0.50
# DOOD(0.582), OP(0.484), STEEM(0.377) 포함 가능하도록
# ──────────────────────────────────────────────────────────────
pt_regex(
    "core/engine_cycle.py",
    r'(_REPLACE_SCORE\s*=\s*)0\.80',
    r'\g<1>0.50',
    "FX5-1 교체매매 score 임계 0.50"
)

# ──────────────────────────────────────────────────────────────
# FX5-2: PENDING-QUEUE 기본 TTL 1800s(30분) → 3600s(60분)
# 10분 TTL 코인도 재감지 루프로 재추가되므로 실질 보장
# ──────────────────────────────────────────────────────────────
pt_regex(
    "core/engine_cycle.py",
    r'(_TTL_SEC\s*=\s*)1800',
    r'\g<1>3600',
    "FX5-2 PENDING TTL 3600s"
)

# ──────────────────────────────────────────────────────────────
# FX5-3: 교체매매 최소 손실 기준 완화 -1.5% → -0.8%
# F(SL 8.2 진입가 8.3083) 같이 손실 미미한 포지션도 교체 가능
# ──────────────────────────────────────────────────────────────
pt_regex(
    "core/engine_cycle.py",
    r'(_REPLACE_PNL\s*=\s*)-1\.5',
    r'\g<1>-0.8',
    "FX5-3 교체매매 PNL 기준 -0.8%"
)

# ──────────────────────────────────────────────────────────────
# FX5-4: REST API 재시도 횟수 강화 (getaddrinfo failed 대응)
# rest_collector.py의 retry 횟수 확인 후 3→5로 증가
# ──────────────────────────────────────────────────────────────
pt_regex(
    "data/collectors/rest_collector.py",
    r'(max_retries\s*=\s*)3',
    r'\g<1>5',
    "FX5-4 REST 재시도 3→5"
)

# ──────────────────────────────────────────────────────────────
# FX5-5: 교체매매 최소 보유시간 30분 → 20분
# 포화 상태에서 신규 Surge 코인 더 빨리 진입 허용
# ──────────────────────────────────────────────────────────────
pt_regex(
    "core/engine_cycle.py",
    r'(_REPLACE_HOLD\s*=\s*)30',
    r'\g<1>20',
    "FX5-5 교체매매 최소보유 20분"
)

print(f"\n{'='*60}")
print(f"  fix5 결과  {TS}")
print(f"{'='*60}")
print(f"  OK   {len(RES['OK'])}  : {RES['OK']}")
print(f"  SKIP {len(RES['SKIP'])}  : {RES['SKIP']}")
print(f"  FAIL {len(RES['FAIL'])}  : {RES['FAIL']}")
print(f"  백업 : {ARC}")
