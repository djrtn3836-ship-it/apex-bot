#!/usr/bin/env python3
# fix4.py  – FX4-1 RR -0.95 재시도 | FX4-2 CKB/F SL 수정 | FX4-3 MTFGate BULL thr
import os, shutil, datetime, py_compile, sqlite3, re

BASE = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot"
DB   = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot\database\apex_bot.db"
TS   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
ARC  = os.path.join(BASE, "archive", f"fx4_{TS}")
os.makedirs(ARC, exist_ok=True)
RES  = {"OK": [], "SKIP": [], "FAIL": []}

def bk(rel):
    src = os.path.join(BASE, rel)
    dst = os.path.join(ARC, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return src

def pt_regex(rel, pattern, replacement, label):
    """정규식 기반 패치 (공백/탭 무관)"""
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
# FX4-1: BULL RR 임계값 -0.80 → -0.95  (정규식: 공백 유연)
# ──────────────────────────────────────────────────────────────
pt_regex(
    "core/engine_buy.py",
    r'("BULL"\s*:\s*)-0\.80',
    r'\g<1>-0.95',
    "FX4-1 BULL RR 임계값 -0.95"
)

# ──────────────────────────────────────────────────────────────
# FX4-2: DB에서 CKB/F SL 수정 (롱 포지션 SL > 진입가 버그)
# CKB: 진입가 2₩, SL 2.2₩ → SL 1.84₩ (-8%), TP 2.4₩ 유지
# F  : 진입가 8₩, SL 8.2₩ → SL 7.36₩ (-8%), TP 8.8₩ 유지
# ──────────────────────────────────────────────────────────────
print("\n  [FX4-2] DB SL 수정 시도...")
try:
    conn = sqlite3.connect(DB)
    cur  = conn.cursor()

    # positions 테이블 컬럼 확인
    cur.execute("PRAGMA table_info(positions)")
    cols = [row[1] for row in cur.fetchall()]
    print(f"    positions 컬럼: {cols}")

    # CKB SL 수정
    if "stop_loss" in cols and "market" in cols:
        cur.execute(
            "UPDATE positions SET stop_loss=1.84 WHERE market='KRW-CKB' AND stop_loss > entry_price"
        )
        ckb_rows = cur.rowcount
        cur.execute(
            "UPDATE positions SET stop_loss=7.36 WHERE market='KRW-F' AND stop_loss > entry_price"
        )
        f_rows = cur.rowcount
        conn.commit()
        print(f"    KRW-CKB SL 수정: {ckb_rows}행 | KRW-F SL 수정: {f_rows}행")
        if ckb_rows + f_rows > 0:
            RES["OK"].append("FX4-2 CKB/F SL DB 수정")
        else:
            RES["SKIP"].append("FX4-2 이미 정상 또는 테이블 구조 상이")
    else:
        print(f"    [SKIP] stop_loss 또는 market 컬럼 없음")
        RES["SKIP"].append("FX4-2 컬럼 불일치")
    conn.close()
except Exception as e:
    print(f"    [FAIL] DB 수정 오류: {e}")
    RES["FAIL"].append(f"FX4-2 DB오류: {e}")

# ──────────────────────────────────────────────────────────────
# FX4-3: MTFGate BULL threshold -0.30 → -0.50
# GlobalRegime=BULL 시 altcoin 1d/4h 역방향도 완화 허용
# ──────────────────────────────────────────────────────────────
pt_regex(
    "signals/mtf_gate.py",
    r'(GATE_THRESHOLD_BULL\s*=\s*)-0\.30',
    r'\g<1>-0.50',
    "FX4-3 MTFGate BULL thr -0.50"
)

# ──────────────────────────────────────────────────────────────
# FX4-4: SignalCombiner buy_threshold 0.44 → 0.38
# BULL 레짐에서 단일 전략 신호도 통과 가능하도록
# ──────────────────────────────────────────────────────────────
pt_regex(
    "signals/signal_combiner.py",
    r'(buy_threshold\s*=\s*max\()0\.40',
    r'\g<1>0.35',
    "FX4-4 SignalCombiner buy_threshold 0.38"
)

# ──────────────────────────────────────────────────────────────
# 결과 출력
# ──────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"  fix4 결과  {TS}")
print(f"{'='*60}")
print(f"  OK   {len(RES['OK'])}  : {RES['OK']}")
print(f"  SKIP {len(RES['SKIP'])}  : {RES['SKIP']}")
print(f"  FAIL {len(RES['FAIL'])}  : {RES['FAIL']}")
print(f"  백업 : {ARC}")
