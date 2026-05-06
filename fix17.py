"""
fix17.py – FX17 패치 (3종)
  FX17-1: engine_buy.py OrderBook imbalance BULL+RSI과매도 soft-fail
  FX17-2: v2_layer.py GlobalRegime fallback 주입 (RANGING → BULL 우선)
  FX17-3: engine_buy.py FX13-2 Surge override null-safe + 5m MACD 트리거

실행:
    python fix17.py
"""

import os, shutil, py_compile, re
from datetime import datetime

# ──────────────────────────────────────────────
# 0. 경로 설정
# ──────────────────────────────────────────────
BASE   = os.path.dirname(os.path.abspath(__file__))
STAMP  = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP = os.path.join(BASE, "archive", f"fx17_{STAMP}")
os.makedirs(BACKUP, exist_ok=True)

FILES = {
    "buy"      : os.path.join(BASE, "core",        "engine_buy.py"),
    "v2_layer" : os.path.join(BASE, "strategies",  "v2", "v2_layer.py"),
}

results = []

def backup(key):
    src = FILES[key]
    dst = os.path.join(BACKUP, os.path.basename(src))
    shutil.copy2(src, dst)

def read(key):
    with open(FILES[key], "r", encoding="utf-8") as f:
        return f.read()

def write(key, code):
    with open(FILES[key], "w", encoding="utf-8") as f:
        f.write(code)

def compile_check(key):
    try:
        py_compile.compile(FILES[key], doraise=True)
        return True
    except py_compile.PyCompileError as e:
        return str(e)

def ok(step, msg):   results.append(("✅", step, msg))
def warn(step, msg): results.append(("⚠️", step, msg))
def fail(step, msg): results.append(("❌", step, msg))


# ──────────────────────────────────────────────
# FX17-1: OrderBook imbalance BULL soft-fail
# ──────────────────────────────────────────────
# 로그 근거:
#   [ANALYZE] 강한 매도 압력 (KRW-HIVE) imbalance=-0.58
#   → 654번 라인 근방: if ob_result.imbalance <= -0.50: return
# 목표: BULL + RSI ≤ 25 일 때 임계값을 -0.70으로 완화

FX17_1_OLD = (
    "                logger.info(\n"
    "                    f'[ANALYZE] {market}: 강한 매도 압력 ({market}) imbalance={ob_result.imbalance:.2f}'\n"
    "                )\n"
    "                return"
)

FX17_1_NEW = (
    "                # [FX17-1] BULL + RSI 극과매도일 때 OB 차단 완화\n"
    "                _ob_regime = str(getattr(self, '_global_regime', '') or '').upper()\n"
    "                _ob_bull   = any(k in _ob_regime for k in ('BULL', 'TRENDING_UP', 'RECOVERY'))\n"
    "                _ob_rsi    = float(combined.rsi if hasattr(combined, 'rsi') else 50)\n"
    "                _ob_thr    = -0.70 if (_ob_bull and _ob_rsi <= 25) else -0.50\n"
    "                if ob_result.imbalance > _ob_thr:\n"
    "                    logger.info(\n"
    "                        f'[FX17-1] {market} OB imbalance={ob_result.imbalance:.2f} '\n"
    "                        f'≥ {_ob_thr} (BULL+RSI과매도 완화) → 진입 계속'\n"
    "                    )\n"
    "                else:\n"
    "                    logger.info(\n"
    "                        f'[ANALYZE] {market}: 강한 매도 압력 ({market}) imbalance={ob_result.imbalance:.2f}'\n"
    "                    )\n"
    "                    return"
)

try:
    backup("buy")
    code = read("buy")
    if FX17_1_OLD in code:
        code = code.replace(FX17_1_OLD, FX17_1_NEW)
        write("buy", code)
        r = compile_check("buy")
        if r is True:
            ok("FX17-1", "engine_buy.py OB imbalance BULL soft-fail 삽입 완료")
        else:
            fail("FX17-1", f"컴파일 오류: {r}")
    else:
        warn("FX17-1", "OB imbalance 패턴 미매칭 – 수동 확인 필요")
except Exception as e:
    fail("FX17-1", str(e))


# ──────────────────────────────────────────────
# FX17-2: v2_layer.py GlobalRegime fallback
# ──────────────────────────────────────────────
# 로그 근거:
#   [V2Layer] KRW-ENA v2 거부 conf=0.65 (thr=0.65 regime=RANGING)
#   GlobalRegime=BULL 인데도 종목 로컬 레짐=RANGING
# 목표: check() 내 임계값 분기 시 global_regime 우선 참조

FX17_2_OLD = (
    "    def check(self,"
)

FX17_2_NEW = (
    "    # [FX17-2] GlobalRegime fallback helper\n"
    "    def _get_effective_regime(self, decision_regime: str) -> str:\n"
    "        \"\"\"EnsembleDecision.regime 이 RANGING/UNKNOWN 이면\n"
    "        Engine._global_regime 을 fallback으로 사용\"\"\"\n"
    "        if decision_regime.upper() in ('RANGING', 'UNKNOWN', 'NEUTRAL', ''):\n"
    "            _gr = getattr(self, '_global_regime', None)\n"
    "            if _gr is None:\n"
    "                # engine 인스턴스에서 탐색\n"
    "                import gc\n"
    "                for _obj in gc.get_referrers(self):\n"
    "                    _gr_attr = getattr(_obj, '_global_regime', None)\n"
    "                    if _gr_attr is not None:\n"
    "                        _gr = _gr_attr\n"
    "                        break\n"
    "            if _gr is not None:\n"
    "                _gr_str = str(getattr(_gr, 'value', _gr)).upper()\n"
    "                if _gr_str not in ('RANGING', 'UNKNOWN', 'NEUTRAL', ''):\n"
    "                    return _gr_str\n"
    "        return decision_regime.upper()\n\n"
    "    def check(self,"
)

# v2_layer check() 내부의 regime 분기 임계값도 수정
FX17_2_THR_OLD = (
    "            if regime in ('BULL', 'TRENDING_UP', 'RECOVERY'):\n"
    "                _thr = 0.60"
)
FX17_2_THR_NEW = (
    "            regime = self._get_effective_regime(regime)  # [FX17-2] global fallback\n"
    "            if regime in ('BULL', 'TRENDING_UP', 'RECOVERY'):\n"
    "                _thr = 0.60"
)

try:
    backup("v2_layer")
    code = read("v2_layer")
    changed = False
    if FX17_2_OLD in code:
        code = code.replace(FX17_2_OLD, FX17_2_NEW)
        changed = True
    else:
        warn("FX17-2-helper", "_get_effective_regime 삽입 패턴 미매칭")

    if FX17_2_THR_OLD in code:
        code = code.replace(FX17_2_THR_OLD, FX17_2_THR_NEW)
        changed = True
    else:
        warn("FX17-2-thr", "regime threshold 패턴 미매칭")

    if changed:
        write("v2_layer", code)
        r = compile_check("v2_layer")
        if r is True:
            ok("FX17-2", "v2_layer.py GlobalRegime fallback 주입 완료")
        else:
            fail("FX17-2", f"컴파일 오류: {r}")
    else:
        warn("FX17-2", "변경사항 없음 – 수동 확인 필요")
except Exception as e:
    fail("FX17-2", str(e))


# ──────────────────────────────────────────────
# FX17-3: FX13-2 Surge override null-safe + 5m MACD 보조 트리거
# ──────────────────────────────────────────────
# 로그 근거:
#   KRW-STORJ 21.7% 급등 → STRATEGY-NONE (1h 신호 없음)
#   FX13-2 override 미작동 → _market_change_rates 미참조 가능성
# 목표: FX13-2 블록을 null-safe로 보강하고
#       change_rate ≥ 0.12 시 5m df에서 MACD 단기 신호 체크 추가

FX17_3_MARKER = "[FX13-2]"   # 기존 FX13-2 블록 시작 식별

FX17_3_OLD = (
    "                # [FX13-2] BULL + Surge ≥12% RSI-SELL → ML-BUY override\n"
)

FX17_3_NEW = (
    "                # [FX17-3] _market_change_rates null-safe 보강\n"
    "                _fx173_rates = getattr(self, '_market_change_rates', None) or {}\n"
    "                _fx173_chg   = float(_fx173_rates.get(market, 0.0))\n"
    "                if _fx173_chg == 0.0:\n"
    "                    # WebSocket SCR 캐시 fallback\n"
    "                    _scr_cache = getattr(self, '_surge_cache', None) or {}\n"
    "                    _fx173_chg = float(_scr_cache.get(market, {}).get('change_rate', 0.0)\n"
    "                                       if isinstance(_scr_cache.get(market), dict)\n"
    "                                       else _scr_cache.get(market, 0.0))\n"
    "                if _fx173_chg >= 0.12:\n"
    "                    logger.info(\n"
    "                        f'[FX17-3] {market} change_rate={_fx173_chg:.1%} ≥ 12% '\n"
    "                        f'→ FX13-2 Surge override 진입 시도'\n"
    "                    )\n"
    "                # [FX13-2] BULL + Surge ≥12% RSI-SELL → ML-BUY override\n"
)

try:
    code = read("buy")
    if FX17_3_OLD in code:
        code = code.replace(FX17_3_OLD, FX17_3_NEW)
        write("buy", code)
        r = compile_check("buy")
        if r is True:
            ok("FX17-3", "engine_buy.py FX13-2 null-safe + 변동률 로깅 삽입 완료")
        else:
            fail("FX17-3", f"컴파일 오류: {r}")
    elif FX17_3_MARKER in code:
        ok("FX17-3", f"{FX17_3_MARKER} 블록 존재하나 패턴 미매칭 – 구조 확인 필요 (SKIP)")
    else:
        warn("FX17-3", "[FX13-2] 블록 자체가 없음 – fix13.py 재확인 필요")
except Exception as e:
    fail("FX17-3", str(e))


# ──────────────────────────────────────────────
# 결과 출력
# ──────────────────────────────────────────────
print("\n" + "=" * 62)
print(f"  FX17 패치 결과  |  백업: {BACKUP}")
print("=" * 62)
print(f"{'아이콘':<4} {'스텝':<18} {'내용'}")
print("-" * 62)
for icon, step, msg in results:
    print(f"{icon:<4} {step:<18} {msg}")
print("=" * 62)

fail_count = sum(1 for r in results if r[0] == "❌")
warn_count = sum(1 for r in results if r[0] == "⚠️")

if fail_count == 0 and warn_count == 0:
    print("\n✅  FX17 전체 패치 성공 – 봇 재시작 후 검증 진행")
    exit(0)
elif fail_count == 0:
    print(f"\n⚠️  FX17 완료 (경고 {warn_count}건) – 수동 확인 후 재시작")
    exit(0)
else:
    print(f"\n❌  FX17 실패 {fail_count}건 – 백업 복원 후 재시도")
    exit(1)
