import sys as _sys, os as _os
_os.environ.setdefault("PYTHONIOENCODING", "utf-8")
_os.environ.setdefault("PYTHONUTF8", "1")
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(_sys.stderr, "reconfigure"):
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")

"""APEX BOT   ( )"""
import subprocess
import sys
import os
import warnings

# PPO GPU 경고 완전 억제
warnings.filterwarnings("ignore", category=UserWarning, module="stable_baselines3")

# ── 환경 검증 ──────────────────────────────────────
FORBIDDEN_PATHS = ["venv64", "venv32", "evolution_ultimate_bot"]
FORBIDDEN_PORTS = {5555, 5556, 5557, 5558, 5599}
PYTHON_EXE = sys.executable

for fp in FORBIDDEN_PATHS:
    if fp in PYTHON_EXE:
        print(f" :  ({fp}) !")
        sys.exit(1)

import socket
for port in FORBIDDEN_PORTS:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            print(f"    {port}   ()")

print("   ")
print(f"   Python : {PYTHON_EXE}")
print(f"    : 8888 (), 5600~5604 (ZMQ)")
print(f"    : {sorted(FORBIDDEN_PORTS)} ( )")
print()

# ── PPO 경고 억제 + 봇 실행 ────────────────────────
env = os.environ.copy()
env["PYTHONWARNINGS"] = "ignore::UserWarning:stable_baselines3"
env["PYTHONIOENCODING"] = "utf-8"
env["PYTHONUTF8"] = "1"

result = subprocess.run(
    [sys.executable, "-X", "utf8", "main.py", "--mode", "paper"],
    env=env
)
sys.exit(result.returncode)
