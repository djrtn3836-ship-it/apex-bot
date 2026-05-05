# set_live.py — 라이브 전환 최종 설정
import shutil, os, re, py_compile
from datetime import datetime

BASE      = os.path.dirname(os.path.abspath(__file__))
SETTINGS  = os.path.join(BASE, "config", "settings.py")
ENV_PATH  = os.path.join(BASE, ".env")
BAK_SET   = SETTINGS + f".bak_live_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
BAK_ENV   = ENV_PATH + f".bak_live_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

print("=" * 62)
print("  APEX BOT — 라이브 전환 설정")
print("=" * 62)

# ══════════════════════════════════════════════════════
# STEP 1: settings.py 파라미터 수정
# ══════════════════════════════════════════════════════
print("\n  [ STEP 1 ] config/settings.py 수정")
shutil.copy2(SETTINGS, BAK_SET)
print(f"  백업: {os.path.basename(BAK_SET)}")

lines = open(SETTINGS, encoding="utf-8").readlines()
changes = []

TARGETS = {
    # (패턴, 기존값, 신규값, 설명)
    "max_positions":    (r'(\s+max_positions\s*:\s*int\s*=\s*)\d+',        "15", "5",    "최대 포지션 15→5 (소액 테스트)"),
    "daily_loss_limit": (r'(\s+daily_loss_limit\s*:\s*float\s*=\s*)[\d.]+', "0.05","0.03","일일 손실 한도 5%→3%"),
    "max_position_ratio":(r'(\s+max_position_ratio\s*:\s*float\s*=\s*)[\d.]+', "0.20","0.17","포지션 비율 20%→17%"),
}

for key, (pattern, old, new, desc) in TARGETS.items():
    for i, line in enumerate(lines):
        m = re.match(pattern, line)
        if m:
            lines[i] = re.sub(pattern, lambda m: m.group(1) + new, line)
            changes.append((i+1, desc, old, new))
            break

for ln, desc, old, new in changes:
    print(f"  ✅ L{ln:>4}: {desc} ({old} → {new})")

# 저장 및 컴파일
open(SETTINGS, "w", encoding="utf-8").writelines(lines)
try:
    py_compile.compile(SETTINGS, doraise=True)
    print("  ✅ settings.py 컴파일 OK")
except py_compile.PyCompileError as e:
    print(f"  ❌ 컴파일 실패 → 복원")
    shutil.copy2(BAK_SET, SETTINGS)
    raise SystemExit(1)

# ══════════════════════════════════════════════════════
# STEP 2: .env 파일 LIVE 모드 전환
# ══════════════════════════════════════════════════════
print("\n  [ STEP 2 ] .env 파일 수정")
shutil.copy2(ENV_PATH, BAK_ENV)
print(f"  백업: {os.path.basename(BAK_ENV)}")

env_lines = open(ENV_PATH, encoding="utf-8").readlines()
env_dict  = {}
for line in env_lines:
    if "=" in line and not line.strip().startswith("#"):
        k, _, v = line.partition("=")
        env_dict[k.strip()] = v.strip()

# 변경할 값
env_dict["TRADING_MODE"]      = "live"
env_dict["APEX_LIVE_CONFIRM"] = "yes"
env_dict["INITIAL_CAPITAL"]   = "114553"

# 다시 쓰기 (기존 순서 유지)
new_env_lines = []
updated_keys  = set()
for line in env_lines:
    if "=" in line and not line.strip().startswith("#"):
        k = line.partition("=")[0].strip()
        if k in env_dict:
            new_env_lines.append(f"{k}={env_dict[k]}\n")
            updated_keys.add(k)
        else:
            new_env_lines.append(line)
    else:
        new_env_lines.append(line)

# 새로 추가된 키
for k, v in env_dict.items():
    if k not in updated_keys:
        new_env_lines.append(f"{k}={v}\n")

open(ENV_PATH, "w", encoding="utf-8").writelines(new_env_lines)
print("  ✅ TRADING_MODE   : paper → live")
print("  ✅ APEX_LIVE_CONFIRM: yes")
print("  ✅ INITIAL_CAPITAL : 114553")

# ══════════════════════════════════════════════════════
# STEP 3: 최종 설정 확인
# ══════════════════════════════════════════════════════
print("\n  [ STEP 3 ] 최종 설정 확인")

# settings.py 확인
lines_f = open(SETTINGS, encoding="utf-8").readlines()
check_keys = ["max_positions", "daily_loss_limit", "max_position_ratio",
              "min_order_amount", "total_drawdown_limit", "consecutive_loss_limit"]
for i, line in enumerate(lines_f, 1):
    for k in check_keys:
        if re.match(rf'\s+{k}\s*:', line):
            print(f"  settings L{i:>4}: {line.rstrip()[:70]}")

# .env 확인
print()
from dotenv import load_dotenv
load_dotenv(ENV_PATH, override=True)
import os as _os
print(f"  TRADING_MODE     : {_os.getenv('TRADING_MODE','?').upper()}")
print(f"  APEX_LIVE_CONFIRM: {_os.getenv('APEX_LIVE_CONFIRM','?')}")
print(f"  INITIAL_CAPITAL  : ₩{int(_os.getenv('INITIAL_CAPITAL','0')):,}")

# ══════════════════════════════════════════════════════
# 최종 안내
# ══════════════════════════════════════════════════════
print()
print("=" * 62)
print("  ✅ 라이브 전환 설정 완료")
print("=" * 62)
print("""
  ┌─────────────────────────────────────────────────────┐
  │  라이브 모드 운영 규칙                               │
  ├─────────────────────────────────────────────────────┤
  │  • 총 자본       : ₩114,553                         │
  │  • 1포지션 투자금 : ₩20,000 (자본의 17%)            │
  │  • 최대 포지션 수 : 5개 (총 ₩100,000)              │
  │  • 일일 손실 한도 : -3% (₩3,437 손실 시 중단)      │
  │  • 총 손실 한도   : -15% (₩17,183 손실 시 중단)    │
  │  • 연속 손실 한도 : 5회 연속 손실 시 중단           │
  │  • 출금 권한      : 비활성화 ✅                      │
  ├─────────────────────────────────────────────────────┤
  │  재시작 명령:                                        │
  │  taskkill /F /IM python.exe /T                      │
  │  python main.py                                     │
  └─────────────────────────────────────────────────────┘
""")
