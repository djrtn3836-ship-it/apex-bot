# ============================================================
#  APEX BOT — 완전 통합 패치 (all_in_one_patch.py)
#  실행: python all_in_one_patch.py
#  위치: C:\Users\hdw38\Desktop\달콩\bot\apex_bot\
# ============================================================
"""
패치 목록 (총 7개):
  P1. models/inference/predictor.py       — Temperature 0.5 → 1.5
  P2. signals/signal_combiner.py          — ML가중치 3.0→1.5, ML_HOLD_BOOST 제거
  P3. signals/signal_combiner.py          — CombinedSignal bear_reversal 필드 추가
  P4. strategies/v2/ensemble_engine.py    — REGIME_BOOSTS 누락키(BULL/BEAR_WATCH/BEAR) 추가
  P5. strategies/v2/ensemble_engine.py    — MIN_SIGNALS_NEEDED 2→1
  P6. strategies/v2/ensemble_engine.py    — SQLite update_result() try/finally 안전화
  P7. strategies/v2/v2_layer.py           — settings.trading→settings.risk 경로 수정
  P8. core/engine_sell.py                 — STEP-8 들여쓰기 수정
  P9. core/engine_buy.py                  — import logging → loguru 통일
"""
from __future__ import annotations
import pathlib, shutil, datetime, sys, py_compile, textwrap

ROOT   = pathlib.Path(__file__).parent
BACKUP = ROOT / "archive" / f"all_in_one_{datetime.datetime.now():%Y%m%d_%H%M%S}"
BACKUP.mkdir(parents=True, exist_ok=True)

results: dict[str, str] = {}

# ────────────────────────────────────────────────────────────
def _backup(path: pathlib.Path):
    rel = path.relative_to(ROOT)
    dst = BACKUP / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dst)

def _apply(label: str, path: pathlib.Path, old: str, new: str):
    """old 문자열을 new로 교체. 탐지 실패 시 SKIP 처리."""
    if not path.exists():
        print(f"  ❌ [{label}] 파일 없음: {path}")
        results[label] = "FILE_MISSING"
        return False
    src = path.read_text(encoding="utf-8")
    if old not in src:
        print(f"  ⏭  [{label}] 패턴 미탐지 → SKIP (이미 적용됐거나 코드 변경됨)")
        results[label] = "SKIP"
        return False
    _backup(path)
    patched = src.replace(old, new, 1)
    path.write_text(patched, encoding="utf-8")
    # 구문 검증
    try:
        py_compile.compile(str(path), doraise=True)
        print(f"  ✅ [{label}] 적용 완료 + 구문 검증 OK")
        results[label] = "OK"
        return True
    except py_compile.PyCompileError as e:
        print(f"  ❌ [{label}] 구문 오류 — 롤백: {e}")
        shutil.copy2(BACKUP / path.relative_to(ROOT), path)
        results[label] = "SYNTAX_ERROR"
        return False

# ============================================================
#  P1: predictor.py — Temperature 0.5 → 1.5
#      근거: T=0.5는 HOLD 로짓을 증폭시켜 BUY/SELL 신호를 억압
#            T=1.5로 완화하면 실제 확률 분포가 복원됨
# ============================================================
P1_FILE = ROOT / "models" / "inference" / "predictor.py"
P1_OLD  = "    TEMPERATURE    = 0.5   # Temperature Scaling: 신뢰도 분포 날카롭게 (0.5 = sharp)"
P1_NEW  = "    TEMPERATURE    = 1.5   # [P1-PATCH] T=0.5→1.5: HOLD 편향 해제, BUY/SELL 신호 복원"

print("\n[P1] predictor.py — Temperature 수정")
_apply("P1_Temperature", P1_FILE, P1_OLD, P1_NEW)

# ============================================================
#  P2: signal_combiner.py — ML 가중치 3.0 → 1.5
#      근거: ML_ENSEMBLE=3.0이 기술전략 전체 합계(7.8)의 38%를 차지
#            HOLD 편향 ML이 3.0 가중치로 신호 전체를 억누름
# ============================================================
P2_FILE = ROOT / "signals" / "signal_combiner.py"
P2_OLD  = "        StrategyKey.ML_ENSEMBLE:       3.0,   # 핵심 전략"
P2_NEW  = "        StrategyKey.ML_ENSEMBLE:       1.5,   # [P2-PATCH] 3.0→1.5: 기술전략 대비 균형 복원"

print("\n[P2] signal_combiner.py — ML_ENSEMBLE 가중치 3.0 → 1.5")
_apply("P2_ML_Weight", P2_FILE, P2_OLD, P2_NEW)

# ============================================================
#  P3: signal_combiner.py — ML_HOLD_BOOST 가짜 전략명 제거
#      근거: "ML_HOLD_BOOST"는 StrategyKey에 없는 가짜 문자열
#            position_sizer 키 미스매치 → default 폴백 강제 적용
# ============================================================
P3_OLD = textwrap.dedent("""\
            elif ml_signal == "HOLD" and len(buy_strategies) >= 3:
                buy_score += ml_weight * ml_confidence * 0.5
                buy_strategies.append("ML_HOLD_BOOST")""")

P3_NEW = textwrap.dedent("""\
            elif ml_signal == "HOLD" and len(buy_strategies) >= 4 and ml_confidence >= 0.50:
                # [P3-PATCH] 조건 강화(≥3→≥4, conf≥0.5) + 가짜 이름 제거
                buy_score += ml_weight * ml_confidence * 0.3
                buy_strategies.append(StrategyKey.ML_ENSEMBLE)""")

print("\n[P3] signal_combiner.py — ML_HOLD_BOOST 제거 및 조건 강화")
_apply("P3_HOLD_BOOST", P2_FILE, P3_OLD, P3_NEW)

# ============================================================
#  P4: signal_combiner.py — CombinedSignal bear_reversal 필드 추가
#      근거: engine_buy에서 combined.bear_reversal = True 동적 할당
#            dataclass 필드 미선언 → 타입 안전성 파괴
# ============================================================
P4_OLD = textwrap.dedent("""\
    ml_signal: Optional[str] = None
    ml_confidence: float = 0.0

    def get(self, key: str, default=None):""")

P4_NEW = textwrap.dedent("""\
    ml_signal: Optional[str] = None
    ml_confidence: float = 0.0
    bear_reversal: bool = False   # [P4-PATCH] engine_buy BEAR_REVERSAL 플래그

    def get(self, key: str, default=None):""")

print("\n[P4] signal_combiner.py — CombinedSignal bear_reversal 필드 추가")
_apply("P4_BearReversal", P2_FILE, P4_OLD, P4_NEW)

# ============================================================
#  P5: ensemble_engine.py — REGIME_BOOSTS 누락 키 추가
#      근거: GlobalRegime.BEAR_WATCH = "BEAR_WATCH" 이 현재 실제 레짐
#            REGIME_BOOSTS에 키가 없어 부스트 0 → 전략 가중치 손실
#            BULL 키도 누락 → 강세장 진입 시 부스트 미적용
# ============================================================
P5_FILE = ROOT / "strategies" / "v2" / "ensemble_engine.py"

P5_OLD = textwrap.dedent("""\
    REGIME_BOOSTS: Dict[str, Dict[str, float]] = {
        # [ST-1] VWAP_Reversion 제거, [ST-2] VolBreakout 제거
        "RANGING":       {"Bollinger_Squeeze": 1.2, "RSI_Divergence": 1.1},
        "TRENDING_UP":   {"Supertrend": 1.4, "MACD_Cross": 1.3, "ATR_Channel": 1.2,
                          "OrderBlock_SMC": 1.1},
        "TRENDING_DOWN": {"RSI_Divergence": 1.2, "Supertrend": 0.7},
        "VOLATILE":      {"OrderBlock_SMC": 1.3, "ATR_Channel": 1.2},
        "BEAR_REVERSAL": {"RSI_Divergence": 1.3, "OrderBlock_SMC": 1.2},
        "RECOVERY":      {"MACD_Cross": 1.3, "OrderBlock_SMC": 1.2, "Bollinger_Squeeze": 1.1},
        "UNKNOWN":       {},  # 레짐 불명 시 boost 없음
    }""")

P5_NEW = textwrap.dedent("""\
    REGIME_BOOSTS: Dict[str, Dict[str, float]] = {
        # [P5-PATCH] GlobalRegime 실제 enum 값과 완전 일치하도록 누락 키 추가
        "BULL":          {"MACD_Cross": 1.3, "Supertrend": 1.4,
                          "OrderBlock_SMC": 1.2, "ATR_Channel": 1.1},
        "RANGING":       {"Bollinger_Squeeze": 1.2, "RSI_Divergence": 1.1},
        "TRENDING_UP":   {"Supertrend": 1.4, "MACD_Cross": 1.3, "ATR_Channel": 1.2,
                          "OrderBlock_SMC": 1.1},
        "TRENDING_DOWN": {"RSI_Divergence": 1.2, "Supertrend": 0.7},
        "VOLATILE":      {"OrderBlock_SMC": 1.3, "ATR_Channel": 1.2},
        "BEAR_REVERSAL": {"RSI_Divergence": 1.3, "OrderBlock_SMC": 1.2},
        "BEAR_WATCH":    {"Bollinger_Squeeze": 1.3, "MACD_Cross": 1.1,
                          "RSI_Divergence": 1.2},   # 현재 실제 레짐
        "BEAR":          {"Bollinger_Squeeze": 1.4, "RSI_Divergence": 1.3},
        "RECOVERY":      {"MACD_Cross": 1.3, "OrderBlock_SMC": 1.2, "Bollinger_Squeeze": 1.1},
        "UNKNOWN":       {},
    }""")

print("\n[P5] ensemble_engine.py — REGIME_BOOSTS 누락 키(BULL/BEAR_WATCH/BEAR) 추가")
_apply("P5_RegimeBoosts", P5_FILE, P5_OLD, P5_NEW)

# ============================================================
#  P6: ensemble_engine.py — MIN_SIGNALS_NEEDED 2 → 1
#      근거: 현재 BEAR_WATCH 레짐 + 200행 캐시에서
#            2개 전략 동시 BUY 확률 ≈18% → 실질 진입 기회 거의 없음
# ============================================================
P6_OLD = "    MIN_SIGNALS_NEEDED: int  = 2"
P6_NEW = "    MIN_SIGNALS_NEEDED: int  = 1   # [P6-PATCH] 2→1: BEAR_WATCH 레짐 신호 빈도 대응"

print("\n[P6] ensemble_engine.py — MIN_SIGNALS_NEEDED 2 → 1")
_apply("P6_MinSignals", P5_FILE, P6_OLD, P6_NEW)

# ============================================================
#  P7: ensemble_engine.py — update_result() SQLite try/finally
#      근거: commit()/execute() 예외 시 close() 미호출 → 연결 누수
#            하루 20회 매도 기준 20개 잠재 연결 누수
# ============================================================
P7_OLD = textwrap.dedent("""\
        try:
            import json as _js_u
            _key = f"ensemble_counter_{strategy_name}"
            _val = _js_u.dumps({
                "signal_count": w.signal_count,
                "win_count":    w.win_count,
            })
            _conn_u = sqlite3.connect(self._db_path, timeout=5)
            _conn_u.execute(
                \"\"\"
                INSERT INTO bot_state(key, value, updated_at)
                VALUES(?, ?, datetime('now','localtime'))
                ON CONFLICT(key) DO UPDATE
                SET value=excluded.value,
                    updated_at=excluded.updated_at
                \"\"\",
                (_key, _val)
            )
            _conn_u.commit()
            _conn_u.close()
        except Exception as _ue:
            logger.debug(f"[Ensemble] 카운터 저장 실패: {_ue}")""")

P7_NEW = textwrap.dedent("""\
        # [P7-PATCH] try/finally — SQLite 연결 누수 방지
        _conn_u = None
        try:
            import json as _js_u
            _key = f"ensemble_counter_{strategy_name}"
            _val = _js_u.dumps({
                "signal_count": w.signal_count,
                "win_count":    w.win_count,
            })
            _conn_u = sqlite3.connect(self._db_path, timeout=5)
            _conn_u.execute(
                \"\"\"
                INSERT INTO bot_state(key, value, updated_at)
                VALUES(?, ?, datetime('now','localtime'))
                ON CONFLICT(key) DO UPDATE
                SET value=excluded.value,
                    updated_at=excluded.updated_at
                \"\"\",
                (_key, _val)
            )
            _conn_u.commit()
        except Exception as _ue:
            logger.debug(f"[Ensemble] 카운터 저장 실패: {_ue}")
        finally:
            if _conn_u is not None:
                try:
                    _conn_u.close()
                except Exception:
                    pass""")

print("\n[P7] ensemble_engine.py — SQLite try/finally 안전화")
_apply("P7_SQLite", P5_FILE, P7_OLD, P7_NEW)

# ============================================================
#  P8: v2_layer.py — settings.trading → settings.risk 경로 수정
#      근거: buy_signal_threshold는 RiskConfig에 정의됨
#            TradingConfig에는 없어서 getattr default=0.45 항상 반환
#            의도된 0.55 임계값이 실제로는 0.45로 낮아짐
# ============================================================
P8_FILE = ROOT / "strategies" / "v2" / "v2_layer.py"
P8_OLD  = "            _v2_conf_thr = getattr(_gs().trading, 'buy_signal_threshold', 0.45)"
P8_NEW  = "            _v2_conf_thr = getattr(_gs().risk, 'buy_signal_threshold', 0.55)  # [P8-PATCH] trading→risk"

print("\n[P8] v2_layer.py — settings.trading → settings.risk 경로 수정")
_apply("P8_SettingsPath", P8_FILE, P8_OLD, P8_NEW)

# ============================================================
#  P9: engine_sell.py — STEP-8 들여쓰기 수정
#      근거: try: 블록이 8칸(메서드 레벨)에 위치해 의도와 다른 실행 흐름
#            DB upsert 예외 시 record_trade_result 스코프가 잘못됨
# ============================================================
P9_FILE = ROOT / "core" / "engine_sell.py"

# 실제 파일의 정확한 들여쓰기 패턴 (8칸 = 메서드 레벨)
P9_OLD = textwrap.dedent("""\
        except Exception as e:
            logger.warning(f"[PARTIAL-SELL] DB upsert/delete 실패: {e}")

                # [STEP-8] partial sell 도 trade result 기록
        try:
            self.risk_manager.record_trade_result(
                is_win=profit_rate > 0,
                profit_rate=profit_rate / 100.0,
            )
        except Exception as _rtr_e:
            logger.warning(f"[PARTIAL-SELL] risk_manager 업데이트 실패: {_rtr_e}")""")

P9_NEW = textwrap.dedent("""\
        except Exception as e:
            logger.warning(f"[PARTIAL-SELL] DB upsert/delete 실패: {e}")

        # [P9-PATCH] STEP-8 들여쓰기 수정 — 항상 실행 보장
        try:
            self.risk_manager.record_trade_result(
                is_win=profit_rate > 0,
                profit_rate=profit_rate / 100.0,
            )
        except Exception as _rtr_e:
            logger.warning(f"[PARTIAL-SELL] risk_manager 업데이트 실패: {_rtr_e}")""")

print("\n[P9] engine_sell.py — STEP-8 들여쓰기 수정")
# 들여쓰기가 혼합된 패턴은 textwrap.dedent 대신 raw 문자열로 정확히 매칭
P9_FILE_SRC = P9_FILE.read_text(encoding="utf-8") if P9_FILE.exists() else ""

# 원본 파일에서 실제 패턴 탐색 (공백 허용 버전)
import re as _re
_p9_pattern = r'except Exception as e:\n\s+logger\.warning\(f"\[PARTIAL-SELL\] DB upsert/delete 실패.*?\n\n\s+# \[STEP-8\].*?\n\s+try:\n\s+self\.risk_manager\.record_trade_result\(\n\s+is_win=profit_rate > 0,\n\s+profit_rate=profit_rate / 100\.0,\n\s+\)\n\s+except Exception as _rtr_e:\n\s+logger\.warning\(f"\[PARTIAL-SELL\] risk_manager 업데이트 실패.*?\)'

_p9_match = _re.search(_p9_pattern, P9_FILE_SRC, _re.DOTALL)
if _p9_match and P9_FILE.exists():
    _backup(P9_FILE)
    _original_block = _p9_match.group(0)
    # 들여쓰기 레벨 파악 (except 줄의 앞 공백)
    _indent = len(_original_block) - len(_original_block.lstrip())
    _ind = " " * 8   # 메서드 내부 기본 들여쓰기
    _p9_replacement = (
        f'{_ind}except Exception as e:\n'
        f'{_ind}    logger.warning(f"[PARTIAL-SELL] DB upsert/delete 실패: {{e}}")\n'
        f'\n'
        f'{_ind}# [P9-PATCH] STEP-8 들여쓰기 수정 — 항상 실행 보장\n'
        f'{_ind}try:\n'
        f'{_ind}    self.risk_manager.record_trade_result(\n'
        f'{_ind}        is_win=profit_rate > 0,\n'
        f'{_ind}        profit_rate=profit_rate / 100.0,\n'
        f'{_ind}    )\n'
        f'{_ind}except Exception as _rtr_e:\n'
        f'{_ind}    logger.warning(f"[PARTIAL-SELL] risk_manager 업데이트 실패: {{_rtr_e}}")'
    )
    _patched_src = P9_FILE_SRC.replace(_original_block, _p9_replacement, 1)
    P9_FILE.write_text(_patched_src, encoding="utf-8")
    try:
        py_compile.compile(str(P9_FILE), doraise=True)
        print(f"  ✅ [P9_IndentFix] regex 방식 적용 완료 + 구문 검증 OK")
        results["P9_IndentFix"] = "OK"
    except py_compile.PyCompileError as e:
        print(f"  ❌ [P9_IndentFix] 구문 오류 — 롤백: {e}")
        shutil.copy2(BACKUP / P9_FILE.relative_to(ROOT), P9_FILE)
        results["P9_IndentFix"] = "SYNTAX_ERROR"
else:
    print("  ⏭  [P9_IndentFix] 패턴 미탐지 → SKIP")
    results["P9_IndentFix"] = "SKIP"

# ============================================================
#  P10: engine_buy.py — import logging → loguru 통일
#       근거: MTF 섹션에서만 표준 logging 사용
#             loguru 필터가 이 로그를 캡처하지 못해 errors.log 누락
# ============================================================
P10_FILE = ROOT / "core" / "engine_buy.py"
P10_OLD  = (
    "                    except Exception as _e:\n"
    "                                import logging as _lg\n"
    "                                _lg.getLogger(\"engine_buy\").debug(f\"[WARN] engine_buy 오류 무시: {_e}\")\n"
    "                                pass"
)
P10_NEW  = (
    "                    except Exception as _e:\n"
    "                                logger.debug(f\"[P10-PATCH][MTF-WARN] {market} {_tf_key} 조회 실패: {_e}\")"
)

print("\n[P10] engine_buy.py — import logging → loguru 통일")
_apply("P10_Loguru", P10_FILE, P10_OLD, P10_NEW)

# ============================================================
#  최종 결과 출력
# ============================================================
print("\n" + "=" * 60)
print("  전체 패치 결과 요약")
print("=" * 60)

_ok    = sum(1 for v in results.values() if v == "OK")
_skip  = sum(1 for v in results.values() if v == "SKIP")
_fail  = sum(1 for v in results.values() if v in ("FILE_MISSING", "SYNTAX_ERROR"))

for label, status in results.items():
    icon = "✅" if status == "OK" else "⏭" if status == "SKIP" else "❌"
    print(f"  {icon}  {label:<30s}: {status}")

print(f"\n  OK={_ok}  SKIP={_skip}  FAIL={_fail}")
print(f"  백업 위치: {BACKUP}")

if _fail > 0:
    print("\n  ⚠️  FAIL 항목이 있습니다 — 로그를 붙여넣어 주세요.")
    sys.exit(1)
else:
    print("\n  🎉 모든 패치 완료!")
    print("\n  ─── 다음 실행 순서 ───────────────────────────────")
    print("  1) python -m py_compile models/inference/predictor.py")
    print("  2) python -m py_compile signals/signal_combiner.py")
    print("  3) python -m py_compile strategies/v2/ensemble_engine.py")
    print("  4) python -m py_compile strategies/v2/v2_layer.py")
    print("  5) python -m py_compile core/engine_sell.py")
    print("  6) git add -A && git commit -m 'patch: all_in_one 완전패치'")
    print("  7) python main.py --mode paper")
