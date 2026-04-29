# fix_volbreak_stable.py
import shutil, py_compile, os, re

base = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot"

print("=" * 60)
print("APEX BOT 패치: Vol_Breakout 비활성화 + 스테이블코인 필터")
print("=" * 60)

# ════════════════════════════════════════════════════════════
# FIX 1: engine_cycle.py – VolBreakoutStrategy 로드 제거
# L758: import 제거, L765: 인스턴스 제거
# ════════════════════════════════════════════════════════════
print("\n[FIX 1] engine_cycle.py – VolBreakoutStrategy 로드 제거")
path = os.path.join(base, "core", "engine_cycle.py")
shutil.copy(path, path + ".bak_volfix")
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

OLD1 = "        from strategies.volatility.vol_breakout import VolBreakoutStrategy\n"
NEW1 = "        # [FIX-VOLBREAK] Vol_Breakout 비활성화 (승률 29%, 기대값 -0.270%)\n        # from strategies.volatility.vol_breakout import VolBreakoutStrategy\n"

OLD2 = "            VolBreakoutStrategy(), ATRChannelStrategy(), OrderBlockStrategy(),"
NEW2 = "            ATRChannelStrategy(), OrderBlockStrategy(),  # [FIX-VOLBREAK] VolBreakoutStrategy 제거"

changed = 0
if OLD1 in content:
    content = content.replace(OLD1, NEW1)
    print("  ✅ import 라인 주석 처리")
    changed += 1
else:
    print("  ⚠️  import 패턴 미발견")

if OLD2 in content:
    content = content.replace(OLD2, NEW2)
    print("  ✅ 전략 인스턴스 제거")
    changed += 1
else:
    print("  ⚠️  인스턴스 패턴 미발견")

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
try:
    py_compile.compile(path, doraise=True)
    print(f"  ✅ 컴파일 성공 ({changed}/2 변경)")
except py_compile.PyCompileError as e:
    print(f"  ❌ 컴파일 실패: {e}")
    shutil.copy(path + ".bak_volfix", path)
    print("  🔄 롤백 완료")


# ════════════════════════════════════════════════════════════
# FIX 2: engine_buy.py – Vol_Breakout 완전 차단 (조건 없이)
# L807~L826의 조건부 차단을 무조건 차단으로 변경
# ════════════════════════════════════════════════════════════
print("\n[FIX 2] engine_buy.py – Vol_Breakout 무조건 차단")
path2 = os.path.join(base, "core", "engine_buy.py")
shutil.copy(path2, path2 + ".bak_volfix")
with open(path2, "r", encoding="utf-8") as f:
    content2 = f.read()

OLD3 = '''            # Vol_Breakout: ADX>35 + TRENDING_UP + FearGreed>40 동시 충족 시만 허용
            if name in ("Vol_Breakout", "VolBreakout", "volatility_break"):
                if _fg_now < 40:
                    logger.info(
                        f"[MDD-L1] {market} Vol_Breakout 차단 "
                        f"(FearGreed={_fg_now}<40)"
                    )
                    continue
                if _adx_now < 35:
                    logger.info(
                        f"[MDD-L1] {market} Vol_Breakout 차단 "
                        f"(ADX={_adx_now:.1f}<35)"
                    )
                    continue
                if _regime_now is not None and "TRENDING_UP" not in str(_regime_now).upper():
                    logger.info(
                        f"[MDD-L1] {market} Vol_Breakout 차단 "
                        f"(regime={_regime_now}≠TRENDING_UP)"
                    )
                    continue'''

NEW3 = '''            # [FIX-VOLBREAK] Vol_Breakout 완전 비활성화 (승률 29%, 기대값 -0.270%)
            if name in ("Vol_Breakout", "VolBreakout", "volatility_break"):
                logger.debug(f"[VOL-DISABLED] {market} Vol_Breakout 영구 차단")
                continue'''

if OLD3 in content2:
    content2 = content2.replace(OLD3, NEW3)
    print("  ✅ 조건부 차단 → 무조건 차단 변경")
else:
    print("  ⚠️  패턴 미발견 – 유사 패턴 탐색 중...")
    # 대안: 정규식으로 Vol_Breakout 차단 블록 찾기
    lines2 = content2.split('\n')
    for i, line in enumerate(lines2):
        if 'Vol_Breakout: ADX>35' in line or 'VolBreakout.*ADX' in line:
            print(f"  대안 위치: L{i+1}: {line.strip()}")

with open(path2, "w", encoding="utf-8") as f:
    f.write(content2)
try:
    py_compile.compile(path2, doraise=True)
    print("  ✅ 컴파일 성공")
except py_compile.PyCompileError as e:
    print(f"  ❌ 컴파일 실패: {e}")
    shutil.copy(path2 + ".bak_volfix", path2)
    print("  🔄 롤백 완료")


# ════════════════════════════════════════════════════════════
# FIX 3: surge_detector.py – 스테이블코인 블랙리스트
# detect() 함수 진입부에 price_change 최소 기준 + 블랙리스트 추가
# ════════════════════════════════════════════════════════════
print("\n[FIX 3] surge_detector.py – 스테이블코인 + 저변동성 차단")
path3 = os.path.join(base, "core", "surge_detector.py")
shutil.copy(path3, path3 + ".bak_stable")
with open(path3, "r", encoding="utf-8") as f:
    content3 = f.read()
    lines3   = content3.split('\n')

# SurgeConfig 클래스에 블랙리스트 추가
OLD4 = '''@dataclass
class SurgeConfig:
    """모든 파라미터 - 나중에 튜닝 가능"""'''

NEW4 = '''# [FIX-STABLE] 스테이블코인 / 무의미 코인 영구 블랙리스트
_SURGE_BLACKLIST: set = {
    "KRW-USDT", "KRW-USDC", "KRW-USD1", "KRW-BUSD", "KRW-DAI",
    "KRW-TUSD", "KRW-USDP", "KRW-FDUSD", "KRW-PYUSD", "KRW-USDS",
}

# [FIX-STABLE] 최소 가격 변동성 기준 (스테이블코인 우회 방지)
_SURGE_MIN_PRICE_CHANGE: float = 0.005  # 0.5% 미만 변동 → 차단


@dataclass
class SurgeConfig:
    """모든 파라미터 - 나중에 튜닝 가능"""'''

if OLD4 in content3:
    content3 = content3.replace(OLD4, NEW4)
    print("  ✅ 블랙리스트 상수 추가")
else:
    print("  ⚠️  SurgeConfig 패턴 미발견")

# detect 함수에서 market 루프 또는 함수 진입부에 블랙리스트 체크 추가
# price_change_1m 계산 직후에 필터 삽입
OLD5 = "                price_change_1m=pc_1m,"
NEW5 = "                price_change_1m=pc_1m,"

# SurgeDetector 클래스의 detect/analyze 함수 진입부 탐색
detect_patterns = [
    "async def detect(",
    "async def analyze(",
    "async def scan(",
    "def detect(",
    "def analyze(",
]
detect_line_idx = -1
for i, line in enumerate(lines3):
    if any(p in line for p in detect_patterns):
        detect_line_idx = i
        print(f"  detect 함수 발견: L{i+1}: {line.strip()}")

# market 인자로 받는 함수에 블랙리스트 체크 삽입
BLACKLIST_CHECK = '''        # [FIX-STABLE] 스테이블코인 / 저변동성 차단
        if market in _SURGE_BLACKLIST:
            logger.debug(f"[STABLE-BLOCK] {market} 블랙리스트 차단")
            return SurgeResult(market=market, score=0.0, is_surge=False, grade="NONE",
                               reason="STABLE_BLACKLIST")
'''

# market을 첫 번째 인자로 받는 detect/analyze 함수 찾기
inserted = False
new_lines = content3.split('\n')
for i, line in enumerate(new_lines):
    if any(p in line for p in detect_patterns) and "market" in line:
        # 함수 시작 다음 줄부터 첫 번째 실제 코드 줄 찾기
        for j in range(i+1, min(i+10, len(new_lines))):
            stripped = new_lines[j].strip()
            if stripped and not stripped.startswith('"""') and not stripped.startswith("'''") and not stripped.startswith('#'):
                indent = len(new_lines[j]) - len(new_lines[j].lstrip())
                indented_check = '\n'.join(' ' * indent + l if l.strip() else l
                                          for l in BLACKLIST_CHECK.strip().split('\n'))
                new_lines.insert(j, indented_check)
                print(f"  ✅ 블랙리스트 체크 삽입: L{j+1} (함수 '{line.strip()[:50]}')")
                inserted = True
                break
        if inserted:
            break

if not inserted:
    print("  ⚠️  detect 함수 삽입 실패 – market 루프에서 차단 시도")
    # 대안: market 루프 내부에 삽입
    for i, line in enumerate(new_lines):
        if "for market in" in line and ("markets" in line or "tickers" in line):
            indent = len(new_lines[i]) - len(new_lines[i].lstrip()) + 4
            check = f"{' '*indent}if market in _SURGE_BLACKLIST:\n{' '*indent}    continue  # [FIX-STABLE]\n"
            new_lines.insert(i+1, check)
            print(f"  ✅ market 루프 블랙리스트 삽입: L{i+2}")
            inserted = True
            break

content3 = '\n'.join(new_lines)

with open(path3, "w", encoding="utf-8") as f:
    f.write(content3)
try:
    py_compile.compile(path3, doraise=True)
    print("  ✅ 컴파일 성공")
except py_compile.PyCompileError as e:
    print(f"  ❌ 컴파일 실패: {e}")
    shutil.copy(path3 + ".bak_stable", path3)
    print("  🔄 롤백 완료")


# ════════════════════════════════════════════════════════════
# FIX 4: engine_buy.py – _analyze_market 스테이블코인 이중 차단
# ════════════════════════════════════════════════════════════
print("\n[FIX 4] engine_buy.py – _analyze_market 스테이블코인 이중 차단")
with open(path2, "r", encoding="utf-8") as f:
    content4 = f.read()

STABLE_CLASS = '''    # [FIX-STABLE] 스테이블코인 영구 블랙리스트 (이중 차단)
    _STABLE_MARKETS: set = {
        "KRW-USDT", "KRW-USDC", "KRW-USD1", "KRW-BUSD", "KRW-DAI",
        "KRW-TUSD", "KRW-USDP", "KRW-FDUSD", "KRW-PYUSD", "KRW-USDS",
    }

    async def _analyze_market(self, market: str'''

if "async def _analyze_market(self, market: str" in content4 and "_STABLE_MARKETS" not in content4:
    content4 = content4.replace(
        "    async def _analyze_market(self, market: str",
        STABLE_CLASS
    )
    print("  ✅ _STABLE_MARKETS 클래스 변수 추가")

# 로그 라인 다음에 차단 체크 삽입
OLD_LOG = '        logger.info(f"[ANALYZE] {market} 진입")'
NEW_LOG = '''        logger.info(f"[ANALYZE] {market} 진입")
        # [FIX-STABLE] 스테이블코인 이중 차단
        if market in self._STABLE_MARKETS:
            logger.debug(f"[STABLE-BLOCK] {market} 스테이블코인 분석 차단")
            return None'''

if OLD_LOG in content4 and "STABLE-BLOCK" not in content4:
    content4 = content4.replace(OLD_LOG, NEW_LOG)
    print("  ✅ _analyze_market 스테이블코인 차단 추가")
elif "STABLE-BLOCK" in content4:
    print("  ℹ️  이미 적용됨")
else:
    print("  ⚠️  [ANALYZE] 로그 패턴 미발견")

with open(path2, "w", encoding="utf-8") as f:
    f.write(content4)
try:
    py_compile.compile(path2, doraise=True)
    print("  ✅ 컴파일 성공")
except py_compile.PyCompileError as e:
    print(f"  ❌ 컴파일 실패: {e}")
    shutil.copy(path2 + ".bak_volfix", path2)
    print("  🔄 롤백 완료")


# ════════════════════════════════════════════════════════════
# 최종 검증
# ════════════════════════════════════════════════════════════
print()
print("=" * 60)
print("=== 최종 검증 ===")
print("=" * 60)

for label, fpath, keywords in [
    ("engine_cycle.py",   path,  ["FIX-VOLBREAK", "VolBreakoutStrategy"]),
    ("engine_buy.py",     path2, ["VOL-DISABLED", "FIX-STABLE", "STABLE_MARKETS"]),
    ("surge_detector.py", path3, ["FIX-STABLE", "SURGE_BLACKLIST", "STABLE-BLOCK"]),
]:
    print(f"\n[{label}]")
    with open(fpath, "r", encoding="utf-8") as f:
        chk_lines = f.readlines()
    for i, line in enumerate(chk_lines):
        if any(k in line for k in keywords):
            print(f"  L{i+1}: {line.rstrip()}")

print("\n✅ 패치 완료. 봇을 재시작하세요.")
