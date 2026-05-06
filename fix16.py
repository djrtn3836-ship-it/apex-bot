"""
fix16.py – FX16 패치 (3종)
  FX16-1: MTFSignalMerger BULL 레짐 RSI 과매도 soft-fail 확장
  FX16-2: engine_cycle.py SL-BAN 만료 재스캔 트리거
  FX16-3: engine_buy.py partial_exit.add_position() 연결 검증 및 삽입

실행:
    python fix16.py
"""

import os, shutil, py_compile, re
from datetime import datetime

# ──────────────────────────────────────────────
# 0. 경로 설정
# ──────────────────────────────────────────────
BASE   = os.path.dirname(os.path.abspath(__file__))
STAMP  = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP = os.path.join(BASE, "archive", f"fx16_{STAMP}")
os.makedirs(BACKUP, exist_ok=True)

FILES = {
    "mtf_merger" : os.path.join(BASE, "signals",   "mtf_signal_merger.py"),
    "cycle"      : os.path.join(BASE, "core",       "engine_cycle.py"),
    "buy"        : os.path.join(BASE, "core",       "engine_buy.py"),
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

def ok(step, msg):
    results.append(("✅", step, msg))

def warn(step, msg):
    results.append(("⚠️", step, msg))

def fail(step, msg):
    results.append(("❌", step, msg))

# ──────────────────────────────────────────────
# FX16-1: MTFSignalMerger BULL soft-fail 확장
# ──────────────────────────────────────────────
# 목표: _merge() 내 allow_buy 계산 직전에
#       BULL 레짐 + RSI ≤ 25일 때 1h DOWN 가중치를 0.05로 낮추고,
#       score 재계산 후 allow_buy 판단

FX16_1_OLD = (
    "        allow_buy  = score > 0.2 and not higher_down and mid_agreement\n"
    "        allow_sell = score < -0.2 and not higher_up"
)

FX16_1_NEW = (
    "        # [FX16-1] BULL 레짐 RSI 과매도 soft-fail 확장\n"
    "        # global_regime 은 인스턴스 속성 또는 호출부에서 주입\n"
    "        _regime_str = str(getattr(self, '_global_regime', '') or '').upper()\n"
    "        _is_bull_regime = any(k in _regime_str for k in ('BULL', 'TRENDING_UP', 'RECOVERY'))\n"
    "        _rsi_oversold   = avg_rsi <= 25\n"
    "        if _is_bull_regime and _rsi_oversold and higher_down:\n"
    "            # 1h DOWN 신호의 실제 가중치를 0.05로 낮춰 score 재계산\n"
    "            _adj_score = 0.0\n"
    "            _adj_wt    = 0.0\n"
    "            for _s in signals:\n"
    "                _w = 0.05 if (_s.timeframe == '1h' and\n"
    "                              _s.direction.value < 0) else _s.weight\n"
    "                _adj_score += _s.direction.value * _s.strength * _w\n"
    "                _adj_wt    += _w\n"
    "            score = (_adj_score / (_adj_wt or 1)) + rsi_bonus\n"
    "            score = score + (tf_bonus if score > 0 else -tf_bonus)\n"
    "            higher_down = False  # soft-fail: 거부권 해제\n"
    "            logger.info(\n"
    "                f'[FX16-1] {\"MTFMerger\"} BULL+RSI과매도({avg_rsi:.0f}) '\n"
    "                f'1h DOWN soft-fail → score재계산={score:.3f}'\n"
    "            )\n"
    "        allow_buy  = score > 0.2 and not higher_down and mid_agreement\n"
    "        allow_sell = score < -0.2 and not higher_up"
)

try:
    backup("mtf_merger")
    code = read("mtf_merger")
    if FX16_1_OLD in code:
        code = code.replace(FX16_1_OLD, FX16_1_NEW)
        write("mtf_merger", code)
        r = compile_check("mtf_merger")
        if r is True:
            ok("FX16-1", "MTFSignalMerger BULL+RSI≤25 soft-fail 삽입 완료")
        else:
            fail("FX16-1", f"컴파일 오류: {r}")
    else:
        warn("FX16-1", "패턴 미매칭 – 이미 패치되었거나 코드 변경됨 (수동 확인 필요)")
except Exception as e:
    fail("FX16-1", str(e))

# ──────────────────────────────────────────────
# FX16-2: engine_cycle.py SL-BAN 만료 재스캔 트리거
# ──────────────────────────────────────────────
# 목표: _cycle() 내 신규 매수 스캔 직전에
#       SL-BAN 만료 종목을 _targets 최우선으로 삽입

FX16_2_OLD = (
    "                if _surge_priority:\n"
    "                    logger.info(f\"[SURGE-INJECT] SurgeCache→targets 삽입: {_surge_priority}\")"
)

FX16_2_NEW = (
    "                # [FX16-2] SL-BAN 만료 종목 최우선 재스캔 트리거\n"
    "                _slban_expired = []\n"
    "                try:\n"
    "                    _slban_dict = getattr(self, '_sl_ban_markets', {})\n"
    "                    import time as _fx16_t\n"
    "                    _now16 = _fx16_t.time()\n"
    "                    for _bm, _bexp in list(_slban_dict.items()):\n"
    "                        # _bexp: 만료 Unix timestamp\n"
    "                        _remaining = _bexp - _now16\n"
    "                        if -60 <= _remaining <= 120:  # 만료 1분 전~2분 후\n"
    "                            if _bm not in _open_now and _bm not in _buying_now:\n"
    "                                _slban_expired.append(_bm)\n"
    "                                logger.info(\n"
    "                                    f'[FX16-2] SL-BAN 만료 감지: {_bm} '\n"
    "                                    f'(잔여={_remaining:.0f}s) → 재스캔 우선 삽입'\n"
    "                                )\n"
    "                except Exception as _fx16_e:\n"
    "                    logger.debug(f'[FX16-2] SL-BAN 만료 체크 오류: {_fx16_e}')\n"
    "                # SL-BAN 만료 코인을 최우선 삽입\n"
    "                for _se in _slban_expired:\n"
    "                    if _se not in _combined:\n"
    "                        _combined.insert(0, _se)\n"
    "                _targets = _combined[:15]\n"
    "                if _surge_priority:\n"
    "                    logger.info(f\"[SURGE-INJECT] SurgeCache→targets 삽입: {_surge_priority}\")"
)

try:
    backup("cycle")
    code = read("cycle")
    if FX16_2_OLD in code:
        code = code.replace(FX16_2_OLD, FX16_2_NEW)
        write("cycle", code)
        r = compile_check("cycle")
        if r is True:
            ok("FX16-2", "engine_cycle.py SL-BAN 만료 재스캔 트리거 삽입 완료")
        else:
            fail("FX16-2", f"컴파일 오류: {r}")
    else:
        warn("FX16-2", "패턴 미매칭 – 이미 패치되었거나 코드 변경됨 (수동 확인 필요)")
except Exception as e:
    fail("FX16-2", str(e))

# ──────────────────────────────────────────────
# FX16-3: engine_buy.py partial_exit.add_position() 연결 검증 및 삽입
# ──────────────────────────────────────────────
# 목표: _execute_buy() 내 매수 성공 직후
#       partial_exit.add_position() 호출이 없으면 삽입

FX16_3_MARKER  = "partial_exit.add_position"   # 이미 존재하면 SKIP
FX16_3_ANCHOR  = "logger.info(f\"[BUY-OK]"      # 매수 성공 로그 직후 삽입

FX16_3_INSERT = (
    "\n"
    "                # [FX16-3] PartialExit 연결 – 매수 성공 시 반드시 등록\n"
    "                try:\n"
    "                    if hasattr(self, 'partial_exit') and self.partial_exit is not None:\n"
    "                        _pe_tp = exec_price * (\n"
    "                            1 + getattr(\n"
    "                                getattr(self, 'settings', None) and\n"
    "                                getattr(self.settings, 'risk', None),\n"
    "                                'take_profit_pct', 0.05\n"
    "                            )\n"
    "                        )\n"
    "                        self.partial_exit.add_position(\n"
    "                            market      = market,\n"
    "                            entry_price = exec_price,\n"
    "                            volume      = exec_volume,\n"
    "                            take_profit = _pe_tp,\n"
    "                        )\n"
    "                        logger.info(\n"
    "                            f'[FX16-3] PartialExit 등록: {market} '\n"
    "                            f'entry={exec_price:,.0f} vol={exec_volume:.6f} '\n"
    "                            f'TP={_pe_tp:,.0f}'\n"
    "                        )\n"
    "                except Exception as _pe_e:\n"
    "                    logger.debug(f'[FX16-3] PartialExit 등록 오류: {_pe_e}')\n"
)

try:
    backup("buy")
    code = read("buy")
    if FX16_3_MARKER in code:
        ok("FX16-3", "partial_exit.add_position() 이미 존재 → SKIP (중복 삽입 방지)")
    else:
        # BUY-OK 로그 라인 이후에 삽입
        # 패턴: logger.info(f"[BUY-OK] ... 로 시작하는 라인 + 줄바꿈 뒤
        pattern = r'([ \t]*logger\.info\(f"\[BUY-OK\][^\n]*\)\n)'
        match   = re.search(pattern, code)
        if match:
            insert_pos = match.end()
            code = code[:insert_pos] + FX16_3_INSERT + code[insert_pos:]
            write("buy", code)
            r = compile_check("buy")
            if r is True:
                ok("FX16-3", "engine_buy.py partial_exit.add_position() 삽입 완료")
            else:
                fail("FX16-3", f"컴파일 오류: {r}")
        else:
            warn("FX16-3", "[BUY-OK] 로그 패턴 미발견 – 수동 삽입 필요")
except Exception as e:
    fail("FX16-3", str(e))

# ──────────────────────────────────────────────
# 결과 출력
# ──────────────────────────────────────────────
print("\n" + "="*60)
print(f"  FX16 패치 결과  |  백업: {BACKUP}")
print("="*60)
print(f"{'아이콘':<4} {'스텝':<14} {'내용'}")
print("-"*60)
for icon, step, msg in results:
    print(f"{icon:<4} {step:<14} {msg}")
print("="*60)

fail_count = sum(1 for r in results if r[0] == "❌")
warn_count = sum(1 for r in results if r[0] == "⚠️")

if fail_count == 0 and warn_count == 0:
    print("\n✅  FX16 전체 패치 성공 – 봇 재시작 후 검증 진행")
    exit(0)
elif fail_count == 0:
    print(f"\n⚠️  FX16 패치 완료 (경고 {warn_count}건) – 수동 확인 후 재시작")
    exit(0)
else:
    print(f"\n❌  FX16 패치 실패 {fail_count}건 – 백업에서 복원 후 재시도")
    exit(1)
