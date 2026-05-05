#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
telegram_fix.py
FIX-G: engine.py Telegram 초기화 크래시 방어
- 실제 줄 번호를 직접 읽어서 패턴을 찾아 교체
"""
import os, shutil, datetime, py_compile

BASE = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot"
TS = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
fp = os.path.join(BASE, "core", "engine.py")

with open(fp, encoding="utf-8") as f:
    src = f.read()

# 실제 파일에서 telegram.initialize 패턴 찾기
TARGET = "await self.telegram.initialize(engine_ref=self)"

if TARGET not in src:
    print("[SKIP] 패턴 없음 - 이미 적용되었거나 코드가 다릅니다")
    print()
    # 근처 코드 출력으로 확인
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if "telegram" in line.lower() and "initialize" in line.lower():
            start = max(0, i-3)
            end = min(len(lines), i+4)
            print(f"  발견된 유사 패턴 (줄 {i+1}):")
            for j in range(start, end):
                print(f"  {j+1:4d} | {lines[j]}")
            print()
else:
    # 앞뒤 들여쓰기 컨텍스트 파악
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if TARGET in line:
            indent = len(line) - len(line.lstrip())
            ind = " " * indent
            print(f"  발견: 줄 {i+1}, 들여쓰기 {indent}칸")
            print(f"  내용: [{line}]")

            # 교체 코드 — 실제 들여쓰기 반영
            OLD = line.rstrip()
            NEW = (
                f"{ind}try:  # FIX-G\n"
                f"{ind}    await self.telegram.initialize(engine_ref=self)\n"
                f"{ind}except Exception as _tg_e:\n"
                f'{ind}    self.logger.warning(f"[FIX-G] Telegram 초기화 실패(무시): {{_tg_e}}")'
            )

            # 백업
            bk = fp + f".bak_{TS}"
            shutil.copy2(fp, bk)
            print(f"  백업: {bk}")

            new_src = src.replace(OLD, NEW, 1)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(new_src)

            try:
                py_compile.compile(fp, doraise=True)
                print("  [OK] FIX-G 적용 완료")
            except py_compile.PyCompileError as e:
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(src)
                print(f"  [FAIL] 컴파일 오류 → 롤백: {e}")
            break
