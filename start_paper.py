"""
APEX BOT 시작 스크립트 (코인봇 전용)
"""
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
        print(f"❌ 오류: 키움봇 환경({fp})으로 실행됨!")
        sys.exit(1)

import socket
for port in FORBIDDEN_PORTS:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            print(f"⚠️  키움봇 포트 {port} 활성 확인 (정상)")

print("✅ 환경 검증 통과")
print(f"   Python : {PYTHON_EXE}")
print(f"   사용 포트: 8888 (대시보드), 5600~5604 (ZMQ)")
print(f"   금지 포트: {sorted(FORBIDDEN_PORTS)} (키움봇 전용)")
print()

# ── PPO 경고 억제 + 봇 실행 ────────────────────────
env = os.environ.copy()
env["PYTHONWARNINGS"] = "ignore::UserWarning:stable_baselines3"

result = subprocess.run(
    [sys.executable, "main.py", "--mode", "paper"],
    env=env
)
sys.exit(result.returncode)
