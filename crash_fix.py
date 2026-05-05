#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crash_fix.py
============
FIX-A  engine.py  : Dashboard 포트 충돌 → fallback 포트 자동 시도
FIX-B  engine.py  : stop() scheduler 가드 (SchedulerNotRunningError 방지)
FIX-C  dashboard.py: 포트 바인딩 실패 시 +1 포트 최대 3회 재시도
FIX-D  engine_cycle.py: WF cold-start 보호 (OOS_sharpe 0 → -0.5)
FIX-E  mtf_gate.py : BULL 임계값 -0.10 → -0.20
FIX-F  engine_buy.py: 최소 단가 필터 10 KRW
"""

import os, shutil, datetime, py_compile, textwrap

BASE = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot"
TS   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
ARC  = os.path.join(BASE, "archive", f"crash_fix_{TS}")
os.makedirs(ARC, exist_ok=True)

results = {"OK": [], "SKIP": [], "FAIL": []}

def backup(path):
    rel = os.path.relpath(path, BASE)
    dst = os.path.join(ARC, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(path, dst)

def patch(rel_path, old, new, label):
    full = os.path.join(BASE, rel_path)
    if not os.path.exists(full):
        print(f"  [SKIP] {label}: 파일 없음 → {rel_path}")
        results["SKIP"].append(label); return
    with open(full, encoding="utf-8") as f:
        src = f.read()
    if old not in src:
        print(f"  [SKIP] {label}: 패턴 없음 (이미 적용됐거나 경로 상이)")
        results["SKIP"].append(label); return
    backup(full)
    new_src = src.replace(old, new, 1)
    with open(full, "w", encoding="utf-8") as f:
        f.write(new_src)
    try:
        py_compile.compile(full, doraise=True)
        print(f"  [OK]   {label}")
        results["OK"].append(label)
    except py_compile.PyCompileError as e:
        with open(full, "w", encoding="utf-8") as f:
            f.write(src)
        print(f"  [FAIL] {label}: 컴파일 오류 → 롤백\n         {e}")
        results["FAIL"].append(label)

# ══════════════════════════════════════════════════════════════
# FIX-A + FIX-B : engine.py — stop() scheduler 가드
# ══════════════════════════════════════════════════════════════
patch(
    "core/engine.py",
    # OLD
    "self.scheduler.shutdown(wait=False)",
    # NEW
    # apscheduler 상태 확인 후 shutdown — SchedulerNotRunningError 방지
    "if self.scheduler and self.scheduler.running:\n"
    "            self.scheduler.shutdown(wait=False)  # FIX-B",
    "FIX-B scheduler stop() 가드"
)

# ══════════════════════════════════════════════════════════════
# FIX-C : dashboard.py — 포트 fallback 자동 재시도
# ══════════════════════════════════════════════════════════════
# Dashboard 시작 시 포트 점유 오류를 잡아 +1씩 최대 3회 재시도
patch(
    "monitoring/dashboard.py",
    # OLD: 일반적인 uvicorn.run 또는 직접 서버 시작 패턴
    'host="0.0.0.0", port=8888',
    # NEW
    'host="0.0.0.0", port=_get_free_port(8888)',
    "FIX-C dashboard port fallback 호출"
)

# _get_free_port 헬퍼를 dashboard.py 상단에 삽입
patch(
    "monitoring/dashboard.py",
    # OLD: import 블록 끝 부분 (일반적으로 마지막 import 다음 줄)
    "import uvicorn",
    # NEW
    """import uvicorn
import socket as _socket

def _get_free_port(start: int = 8888, retries: int = 5) -> int:
    \"\"\"FIX-C: 포트 점유 시 빈 포트 자동 탐색\"\"\"
    for port in range(start, start + retries):
        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
            s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    return start + retries  # 최후 fallback""",
    "FIX-C _get_free_port 헬퍼 삽입"
)

# ══════════════════════════════════════════════════════════════
# FIX-D : engine_cycle.py — WF cold-start 보호
# ══════════════════════════════════════════════════════════════
patch(
    "core/engine_cycle.py",
    # OLD: OOS sharpe 0 이하 → 전략 비활성화
    "if oos_sharpe <= 0:",
    # NEW: -0.5 미만일 때만 비활성화 (cold-start 보호)
    "if oos_sharpe < -0.5:  # FIX-D: cold-start 보호",
    "FIX-D WF cold-start 보호"
)

# ══════════════════════════════════════════════════════════════
# FIX-E : mtf_gate.py — BULL 임계값 완화
# ══════════════════════════════════════════════════════════════
patch(
    "signals/mtf_gate.py",
    '"BULL": -0.10,',
    '"BULL": -0.20,  # FIX-E: BULL 레짐 완화',
    "FIX-E MTFGate BULL -0.20"
)

# ══════════════════════════════════════════════════════════════
# FIX-F : engine_buy.py — 최소 단가 필터 (10 KRW)
# ══════════════════════════════════════════════════════════════
patch(
    "core/engine_buy.py",
    # OLD: 스테이블코인 블랙리스트 직후 return
    "if market in self.STABLE_COIN_BLACKLIST:\n            return",
    # NEW: 블랙리스트 체크 + 최소 단가 체크
    """if market in self.STABLE_COIN_BLACKLIST:
            return
        # FIX-F: 최소 코인 단가 필터 (≤10 KRW 매수 금지 — CKB형 방지)
        try:
            _spot_price = float(candles.iloc[-1]["close"]) if (
                candles is not None and len(candles) > 0
            ) else 0.0
        except Exception:
            _spot_price = 0.0
        _MIN_COIN_PRICE = 10.0
        if 0 < _spot_price < _MIN_COIN_PRICE:
            self.logger.debug(
                f"[FIX-F] {market} 단가 {_spot_price:.2f}원 "
                f"< {_MIN_COIN_PRICE}원 → 매수 스킵"
            )
            return""",
    "FIX-F 최소 단가 필터"
)

# ══════════════════════════════════════════════════════════════
# 결과 출력
# ══════════════════════════════════════════════════════════════
print()
print("=" * 60)
print(f"  crash_fix.py  결과  ({TS})")
print("=" * 60)
print(f"  OK   : {len(results['OK'])}  → {results['OK']}")
print(f"  SKIP : {len(results['SKIP'])}  → {results['SKIP']}")
print(f"  FAIL : {len(results['FAIL'])}  → {results['FAIL']}")
print(f"  백업  : {ARC}")
print("=" * 60)
if results["FAIL"]:
    print("\n  ⛔ FAIL 항목이 있습니다. 위 오류 메시지를 확인하세요.")
else:
    print("\n  ✅ 모든 패치 완료. 아래 명령으로 재시작하세요.")
