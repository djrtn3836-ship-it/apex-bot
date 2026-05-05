#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ws_fix.py
WS-1: WebSocket 스트림 연결 간격 추가 (429 방지)
WS-2: 재연결 backoff 지수 증가
"""
import os, shutil, datetime, py_compile

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
    ns = src.replace(old, new, 1)
    open(fp, "w", encoding="utf-8").write(ns)
    try:
        py_compile.compile(fp, doraise=True)
        print(f"  [OK]   {label}")
    except py_compile.PyCompileError as e:
        open(fp, "w", encoding="utf-8").write(src)
        print(f"  [FAIL] {label}: {e}")

# WS-1: 스트림 초기화 시 각 연결마다 0.5초 대기
patch(
    "core/engine.py",
    "[MULTI-STREAM] WebSocket 초기화",
    "[MULTI-STREAM] WebSocket 초기화 (연결간격 0.5s)",
    "WS-1 로그 마커"
)

# engine.py WebSocket 다중 스트림 생성 루프에 sleep 추가
patch(
    "core/engine.py",
    "for i, chunk in enumerate(chunks):",
    "for i, chunk in enumerate(chunks):\n"
    "                await asyncio.sleep(0.5)  # WS-1: 429 방지",
    "WS-1 스트림 연결 간격"
)

# WS-2: 재연결 실패 시 backoff 1초 → 3초
patch(
    "core/engine_schedule.py",
    "await asyncio.sleep(1)",
    "await asyncio.sleep(3)  # WS-2: backoff 강화",
    "WS-2 재연결 backoff"
)

print("\n완료 — git commit 후 재시작하세요")
