#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, shutil, datetime, py_compile

BASE = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot"
TS = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
ARC = os.path.join(BASE, "archive", f"dfx_{TS}")
os.makedirs(ARC, exist_ok=True)
R = {"OK": [], "SKIP": [], "FAIL": []}

def bk(p):
    r = os.path.relpath(p, BASE)
    d = os.path.join(ARC, r)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    shutil.copy2(p, d)

def pt(rp, old, new, lb):
    fp = os.path.join(BASE, rp)
    if not os.path.exists(fp):
        print(f"  [SKIP] {lb}: 파일없음")
        R["SKIP"].append(lb); return
    src = open(fp, encoding="utf-8").read()
    if old not in src:
        print(f"  [SKIP] {lb}: 패턴없음(이미적용)")
        R["SKIP"].append(lb); return
    bk(fp)
    ns = src.replace(old, new, 1)
    open(fp, "w", encoding="utf-8").write(ns)
    try:
        py_compile.compile(fp, doraise=True)
        print(f"  [OK]   {lb}")
        R["OK"].append(lb)
    except py_compile.PyCompileError as e:
        open(fp, "w", encoding="utf-8").write(src)
        print(f"  [FAIL] {lb}: {e}")
        R["FAIL"].append(lb)

def ti(rp, ic, mk, lb):
    fp = os.path.join(BASE, rp)
    if not os.path.exists(fp):
        print(f"  [SKIP] {lb}: 파일없음")
        R["SKIP"].append(lb); return
    src = open(fp, encoding="utf-8").read()
    if mk in src:
        print(f"  [SKIP] {lb}: 이미적용")
        R["SKIP"].append(lb); return
    bk(fp)
    lines = src.splitlines(keepends=True)
    n = 0
    for i, line in enumerate(lines):
        s = line.lstrip()
        if s.startswith("import ") or s.startswith("from "):
            n = i
            break
    lines.insert(n, ic + "\n")
    open(fp, "w", encoding="utf-8").write("".join(lines))
    try:
        py_compile.compile(fp, doraise=True)
        print(f"  [OK]   {lb}")
        R["OK"].append(lb)
    except py_compile.PyCompileError as e:
        open(fp, "w", encoding="utf-8").write(src)
        print(f"  [FAIL] {lb}: {e}")
        R["FAIL"].append(lb)

HELPER = "\n".join([
    "import socket as _socket",
    "",
    "def _get_free_port(start=8888, retries=5):",
    "    for port in range(start, start + retries):",
    "        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:",
    "            s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)",
    "            try:",
    '                s.bind(("0.0.0.0", port))',
    "                return port",
    "            except OSError:",
    "                continue",
    "    return start + retries  # END_FIX_C",
])

ti(
    "monitoring/dashboard.py",
    HELPER,
    "END_FIX_C",
    "FIX-C _get_free_port 삽입"
)

pt(
    "monitoring/dashboard.py",
    "port=8888",
    "port=_get_free_port(8888)",
    "FIX-C-2 port 교체"
)

pt(
    "core/engine.py",
    "await self.telegram.initialize(engine_ref=self)",
    "\n".join([
        "try:",
        "            await self.telegram.initialize(engine_ref=self)",
        "        except Exception as _tg_e:",
        '            self.logger.warning(f"[FIX-G] Telegram 실패(무시): {_tg_e}")  # FIX-G',
    ]),
    "FIX-G Telegram guard"
)

print()
print("=" * 50)
print(f"  dashboard_fix 결과  {TS}")
print("=" * 50)
print(f"  OK   {len(R['OK'])}  : {R['OK']}")
print(f"  SKIP {len(R['SKIP'])}  : {R['SKIP']}")
print(f"  FAIL {len(R['FAIL'])}  : {R['FAIL']}")
print(f"  백업 : {ARC}")
print("=" * 50)
