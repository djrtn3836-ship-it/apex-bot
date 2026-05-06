"""
fix17b.py – FX17b 패치 (실제 코드 구조 기반 정밀 패치)
  FX17b-1: engine_buy.py  ob_analyzer.can_buy() BULL+RSI soft-fail
  FX17b-2: v2_layer.py    fallback_regime 주입 강화
            (engine_buy.py → v2_layer.check() 호출 시 GlobalRegime 직접 전달)
  FX17b-3: engine_buy.py  FX13-2 _surge_cache 키 구조 통일
            (dict vs float 혼재 방어)

실행:
    python fix17b.py
"""

import os, shutil, py_compile, re
from datetime import datetime

BASE   = os.path.dirname(os.path.abspath(__file__))
STAMP  = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP = os.path.join(BASE, "archive", f"fx17b_{STAMP}")
os.makedirs(BACKUP, exist_ok=True)

FILES = {
    "buy"      : os.path.join(BASE, "core",       "engine_buy.py"),
    "v2_layer" : os.path.join(BASE, "strategies", "v2", "v2_layer.py"),
}

results = []

def backup(key):
    src = FILES[key]
    shutil.copy2(src, os.path.join(BACKUP, os.path.basename(src)))

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


# ══════════════════════════════════════════════
# FX17b-1: engine_buy.py  ob_analyzer.can_buy() BULL+RSI soft-fail
# ══════════════════════════════════════════════
# 실제 코드:
#   can_buy_ob, ob_reason = ob_analyzer.can_buy(ob_signal)
#   if not can_buy_ob and combined.signal_type == SignalType.BUY:
#       logger.info(f"  ({market}): {ob_reason}")
#       return
# →  BULL + RSI ≤ 25 조건에서 imbalance 수치를 직접 꺼내 -0.70 이상이면 통과

FX17B_1_OLD = (
    "                    can_buy_ob, ob_reason = ob_analyzer.can_buy(ob_signal)\n"
    "                    if not can_buy_ob and combined.signal_type == SignalType.BUY:\n"
    "                        logger.info(f\"  ({market}): {ob_reason}\")\n"
    "                        return"
)

FX17B_1_NEW = (
    "                    can_buy_ob, ob_reason = ob_analyzer.can_buy(ob_signal)\n"
    "                    if not can_buy_ob and combined.signal_type == SignalType.BUY:\n"
    "                        # [FX17b-1] BULL + RSI 극과매도 → OB 차단 soft-fail\n"
    "                        _ob17_regime = str(getattr(\n"
    "                            getattr(self, '_global_regime', None), 'value',\n"
    "                            getattr(self, '_global_regime', 'UNKNOWN') or 'UNKNOWN'\n"
    "                        )).upper()\n"
    "                        _ob17_bull = _ob17_regime in ('BULL', 'TRENDING_UP', 'RECOVERY')\n"
    "                        _ob17_imb  = getattr(ob_signal, 'imbalance', -1.0)\n"
    "                        _ob17_rsi  = float(\n"
    "                            df_processed.iloc[-1].get('rsi', 50)\n"
    "                            if hasattr(df_processed, 'iloc') else 50\n"
    "                        )\n"
    "                        # RSI 과매도 여부는 combined.rsi 또는 df 마지막 행 참조\n"
    "                        _ob17_oversold = _ob17_rsi <= 25\n"
    "                        _ob17_thr      = -0.70 if (_ob17_bull and _ob17_oversold) else -1.0\n"
    "                        if _ob17_imb >= _ob17_thr and _ob17_thr > -1.0:\n"
    "                            logger.info(\n"
    "                                f'[FX17b-1] {market} OB can_buy=False이나 '\n"
    "                                f'BULL+RSI과매도({_ob17_rsi:.0f}) imbalance={_ob17_imb:.2f}'\n"
    "                                f' ≥ {_ob17_thr:.2f} → soft-fail 통과'\n"
    "                            )\n"
    "                        else:\n"
    "                            logger.info(f\"  ({market}): {ob_reason}\")\n"
    "                            return"
)

try:
    backup("buy")
    code = read("buy")
    if FX17B_1_OLD in code:
        code = code.replace(FX17B_1_OLD, FX17B_1_NEW)
        write("buy", code)
        r = compile_check("buy")
        if r is True:
            ok("FX17b-1", "engine_buy.py OB can_buy soft-fail 삽입 완료")
        else:
            fail("FX17b-1", f"컴파일 오류: {r}")
    else:
        # 들여쓰기 변형 탐색 (4스페이스 vs 탭)
        alt = FX17B_1_OLD.replace("                    ", "                ")
        if alt in code:
            new_alt = FX17B_1_NEW.replace("                    ", "                ")
            code = code.replace(alt, new_alt)
            write("buy", code)
            r = compile_check("buy")
            if r is True:
                ok("FX17b-1", "engine_buy.py OB soft-fail 삽입 완료 (들여쓰기 보정)")
            else:
                fail("FX17b-1", f"컴파일 오류: {r}")
        else:
            warn("FX17b-1", "OB can_buy 패턴 미매칭 – 수동 확인 필요")
except Exception as e:
    fail("FX17b-1", str(e))


# ══════════════════════════════════════════════
# FX17b-2: v2_layer.py  fallback_regime BULL 인식 강화
# ══════════════════════════════════════════════
# 실제 코드:
#   elif not decision.should_enter and decision.confidence >= 0.65:
#       _fx15_bull_r = str(fallback_regime).upper() in ("BULL","TRENDING_UP","RECOVERY")
#       _fx15_refuse_thr = 0.60 if _fx15_bull_r else 0.65
#       if decision.confidence >= _fx15_refuse_thr:  ← conf=0.65 이면 thr=0.65도 통과, 여전히 차단
#
# 문제: BULL이어도 thr=0.60이고 conf=0.65 이므로 0.65 >= 0.60 → 여전히 차단
# 해결: BULL 레짐에서 should_enter=False + conf < 0.70 이면 거부 취소 후 v1 폴백

FX17B_2_OLD = (
    "                _fx15_bull_r = str(fallback_regime).upper() in (\"BULL\", \"TRENDING_UP\", \"RECOVERY\")\n"
    "                _fx15_refuse_thr = 0.60 if _fx15_bull_r else 0.65\n"
    "                if decision.confidence >= _fx15_refuse_thr:\n"
    "                    _logger.info(\n"
    "                        f\"[V2Layer] {market} v2 거부 conf={decision.confidence:.2f} \"\n"
    "                        f\"(thr={_fx15_refuse_thr:.2f} regime={fallback_regime})\"\n"
    "                    )\n"
    "                    return False, combined_conf, 1.0\n"
    "                else:\n"
    "                    # [FX15-1-A] BULL 레짐 conf 0.60~0.65 구간: 거부 취소 → v1 폴백\n"
    "                    _logger.info(\n"
    "                        f\"[V2Layer] {market} BULL레짐 v2 거부 완화 \"\n"
    "                        f\"conf={decision.confidence:.2f} < thr={_fx15_refuse_thr:.2f} → v1 폴백\"\n"
    "                    )\n"
    "                    return True, v1_confidence, 1.0"
)

FX17B_2_NEW = (
    "                _fx15_bull_r = str(fallback_regime).upper() in (\"BULL\", \"TRENDING_UP\", \"RECOVERY\")\n"
    "                # [FX17b-2] BULL 레짐에서 거부 임계값을 0.70으로 상향\n"
    "                # conf 0.65 수준의 신호가 BULL에서 차단되는 문제 해소\n"
    "                _fx15_refuse_thr = 0.70 if _fx15_bull_r else 0.65\n"
    "                if decision.confidence >= _fx15_refuse_thr:\n"
    "                    _logger.info(\n"
    "                        f\"[V2Layer] {market} v2 거부 conf={decision.confidence:.2f} \"\n"
    "                        f\"(thr={_fx15_refuse_thr:.2f} regime={fallback_regime})\"\n"
    "                    )\n"
    "                    return False, combined_conf, 1.0\n"
    "                else:\n"
    "                    # [FX17b-2] BULL 레짐 conf < 0.70 구간: 거부 취소 → v1 폴백\n"
    "                    _logger.info(\n"
    "                        f\"[FX17b-2] {market} BULL레짐 v2 거부 완화 \"\n"
    "                        f\"conf={decision.confidence:.2f} < thr={_fx15_refuse_thr:.2f} → v1 폴백\"\n"
    "                    )\n"
    "                    return True, v1_confidence, 1.0"
)

try:
    backup("v2_layer")
    code = read("v2_layer")
    if FX17B_2_OLD in code:
        code = code.replace(FX17B_2_OLD, FX17B_2_NEW)
        write("v2_layer", code)
        r = compile_check("v2_layer")
        if r is True:
            ok("FX17b-2", "v2_layer.py BULL 거부 임계값 0.60→0.70 상향 완료")
        else:
            fail("FX17b-2", f"컴파일 오류: {r}")
    else:
        warn("FX17b-2", "v2_layer 패턴 미매칭 – 수동 확인 필요")
except Exception as e:
    fail("FX17b-2", str(e))


# ══════════════════════════════════════════════
# FX17b-3: engine_buy.py  _surge_cache dict/float 혼재 방어 강화
# ══════════════════════════════════════════════
# 실제 코드 (FX14-2 패치 이후):
#   _s_cache = getattr(self, "_surge_cache", {})
#   _fx13_surge = float(_s_cache.get(market, {}).get("change_rate", 0.0)) * 100
# 문제: _surge_cache 값이 float 스칼라인 경우 .get() 호출 → AttributeError
# 해결: isinstance 체크 삽입

FX17B_3_OLD = (
    "                    _s_cache = getattr(self, \"_surge_cache\", {})\n"
    "                    _fx13_surge = float(_s_cache.get(market, {}).get(\"change_rate\", 0.0)) * 100"
)

FX17B_3_NEW = (
    "                    _s_cache = getattr(self, \"_surge_cache\", {})\n"
    "                    # [FX17b-3] dict vs float 혼재 방어\n"
    "                    _s_val = _s_cache.get(market, {})\n"
    "                    if isinstance(_s_val, dict):\n"
    "                        _fx13_surge = float(_s_val.get(\"change_rate\", 0.0)) * 100\n"
    "                    elif isinstance(_s_val, (int, float)):\n"
    "                        _fx13_surge = float(_s_val) * 100\n"
    "                    else:\n"
    "                        _fx13_surge = 0.0"
)

try:
    code = read("buy")
    if FX17B_3_OLD in code:
        code = code.replace(FX17B_3_OLD, FX17B_3_NEW)
        write("buy", code)
        r = compile_check("buy")
        if r is True:
            ok("FX17b-3", "engine_buy.py _surge_cache dict/float 방어 삽입 완료")
        else:
            fail("FX17b-3", f"컴파일 오류: {r}")
    else:
        warn("FX17b-3", "_surge_cache 패턴 미매칭 – 이미 패치됐거나 구조 변경됨")
except Exception as e:
    fail("FX17b-3", str(e))


# ══════════════════════════════════════════════
# 결과 출력
# ══════════════════════════════════════════════
print("\n" + "=" * 64)
print(f"  FX17b 패치 결과  |  백업: {BACKUP}")
print("=" * 64)
print(f"{'아이콘':<4} {'스텝':<18} {'내용'}")
print("-" * 64)
for icon, step, msg in results:
    print(f"{icon:<4} {step:<18} {msg}")
print("=" * 64)

fail_count = sum(1 for r in results if r[0] == "❌")
warn_count = sum(1 for r in results if r[0] == "⚠️")

if fail_count == 0 and warn_count == 0:
    print("\n✅  FX17b 전체 패치 성공 – 봇 재시작 후 검증 진행")
    exit(0)
elif fail_count == 0:
    print(f"\n⚠️  FX17b 완료 (경고 {warn_count}건) – 수동 확인 후 재시작")
    exit(0)
else:
    print(f"\n❌  FX17b 실패 {fail_count}건 – 백업 복원 후 재시도")
    exit(1)
