#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
quick_fix.py
QF-1: weight_boost 0.909 → 1.0 교정 (가중치 감소 방지)
QF-2: target_markets에 급등 감지 종목 추가
QF-3: BULL 레짐 우선 스캔 목록 확장
"""
import os, json, shutil, datetime, py_compile

BASE = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot"
TS   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

def patch(rel, old, new, label):
    fp = os.path.join(BASE, rel)
    if not os.path.exists(fp):
        print(f"  [SKIP] {label}: 파일없음"); return
    src = open(fp, encoding="utf-8").read()
    if old not in src:
        print(f"  [SKIP] {label}: 패턴없음(이미적용)"); return
    bk = fp + f".bak_{TS}"
    shutil.copy2(fp, bk)
    open(fp, "w", encoding="utf-8").write(src.replace(old, new, 1))
    try:
        py_compile.compile(fp, doraise=True)
        print(f"  [OK]   {label}")
    except py_compile.PyCompileError as e:
        open(fp, "w", encoding="utf-8").write(src)
        print(f"  [FAIL] {label}: {e}")

# QF-1: optimized_params.json weight_boost 전부 1.0으로 교정
cfg_path = os.path.join(BASE, "config", "optimized_params.json")
cfg = json.load(open(cfg_path, encoding="utf-8"))
changed = 0
for name, info in cfg["strategies"].items():
    if info.get("weight_boost", 1.0) != 1.0:
        info["weight_boost"] = 1.0
        changed += 1
cfg["updated_at"] = datetime.datetime.now().isoformat()
shutil.copy2(cfg_path, cfg_path + f".bak_{TS}")
json.dump(cfg, open(cfg_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print(f"  [OK]   QF-1 weight_boost → 1.0 ({changed}개 교정)")

# QF-2: target_markets 확장 (10 → 15)
patch(
    "config/settings.py",
    '        "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA",\n'
    '        "KRW-DOGE", "KRW-AVAX", "KRW-DOT", "KRW-LINK", "KRW-ATOM"',
    '        "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA",\n'
    '        "KRW-DOGE", "KRW-AVAX", "KRW-DOT", "KRW-LINK", "KRW-ATOM",\n'
    '        "KRW-HIVE", "KRW-ZIL", "KRW-ALGO", "KRW-ENA", "KRW-CHZ"',
    "QF-2 target_markets 15개"
)

# QF-3: BULL 레짐 우선 스캔 목록 확장
patch(
    "core/engine_cycle.py",
    "'BULL':       ['KRW-BTC','KRW-ETH','KRW-SOL','KRW-XRP','KRW-DOGE','KRW-ADA'],",
    "'BULL':       ['KRW-BTC','KRW-ETH','KRW-SOL','KRW-XRP','KRW-DOGE','KRW-ADA','KRW-HIVE','KRW-ZIL','KRW-ALGO','KRW-ENA'],",
    "QF-3 BULL 레짐 확장"
)

print("\n완료")
