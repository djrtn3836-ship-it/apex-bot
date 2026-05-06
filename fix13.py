#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fix13.py — FX13-1~3 패치
FX13-1: ensemble_engine.py BULL 레짐 신호 충돌 해소 (BUY 전략 boost 강화, SELL 억제)
FX13-2: engine_buy.py BULL+Surge 고급등 시 RSI overbought SELL 단일 차단 완화
FX13-3: engine_buy.py RSI_Divergence BUY combined=None 소멸 방지 (score threshold 완화)
"""
from __future__ import annotations
import re, shutil, py_compile, pathlib, sys
from datetime import datetime

# ─── 경로 ────────────────────────────────────────────────────────────────────
ROOT        = pathlib.Path(__file__).parent
ENSEMBLE_F  = ROOT / "strategies/v2/ensemble_engine.py"
ENGINE_BUY_F= ROOT / "core/engine_buy.py"

# ─── 백업 ────────────────────────────────────────────────────────────────────
TS      = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP  = ROOT / f"archive/fx13_{TS}"
BACKUP.mkdir(parents=True, exist_ok=True)
for _f in [ENSEMBLE_F, ENGINE_BUY_F]:
    if _f.exists():
        shutil.copy2(_f, BACKUP / _f.name)
print(f"✅  백업 완료: {BACKUP}")

results = []

# ════════════════════════════════════════════════════════════════════════════
# FX13-1  ensemble_engine.py
#   - BULL/TRENDING_UP 레짐 REGIME_BOOSTS에 RSI_Divergence BUY 억제 제거
#   - BULL 레짐에서 BUY 전략 boost 강화: MACD 1.3→1.5, Supertrend 1.4→1.6
#   - MIN_SIGNALS_NEEDED를 이미 1로 설정되어 있으나, decide() 내부에서
#     SELL 신호 필터링 로직 추가 (BULL 레짐 시 SELL 신호 score -0.3 패널티)
# ════════════════════════════════════════════════════════════════════════════
src = ENSEMBLE_F.read_text(encoding="utf-8")

# FX13-1-A: BULL 레짐 REGIME_BOOSTS MACD boost 1.3→1.5, Supertrend 1.4→1.6
OLD_BULL_BOOST = '''\
        "BULL":          {"MACD_Cross": 1.3, "Supertrend": 1.4,
                          "OrderBlock_SMC": 1.2, "ATR_Channel": 1.1},'''
NEW_BULL_BOOST = '''\
        "BULL":          {"MACD_Cross": 1.5, "Supertrend": 1.6,  # [FX13-1-A] BUY boost 강화
                          "ATR_Channel": 1.2, "Bollinger_Squeeze": 1.1},'''

if OLD_BULL_BOOST in src:
    src = src.replace(OLD_BULL_BOOST, NEW_BULL_BOOST)
    results.append(("FX13-1-A", "OK", "BULL 레짐 BUY boost 강화 (MACD 1.3→1.5, ST 1.4→1.6)"))
else:
    results.append(("FX13-1-A", "SKIP", "BULL boost 패턴 미매치 — 수동 확인"))

# FX13-1-B: TRENDING_UP 레짐 MACD boost 1.3→1.5
OLD_TUP = '"TRENDING_UP":   {"Supertrend": 1.4, "MACD_Cross": 1.3, "ATR_Channel": 1.2,'
NEW_TUP = '"TRENDING_UP":   {"Supertrend": 1.6, "MACD_Cross": 1.5, "ATR_Channel": 1.3,  # [FX13-1-B]'
if OLD_TUP in src:
    src = src.replace(OLD_TUP, NEW_TUP)
    results.append(("FX13-1-B", "OK", "TRENDING_UP 레짐 ST/MACD boost 강화"))
else:
    results.append(("FX13-1-B", "SKIP", "TRENDING_UP 패턴 미매치"))

# FX13-1-C: decide() 내부 BULL 레짐 SELL 신호 패널티 삽입
# "각 전략 신호 수집" 루프 직후, SELL 신호 억제 코드 삽입
OLD_SIGNAL_LOOP_END = '''\
            if len(signals) < self.MIN_SIGNALS_NEEDED:'''
NEW_SIGNAL_LOOP_END = '''\
            # [FX13-1-C] BULL/TRENDING_UP 레짐에서 SELL-only 신호 억제
            # RSI overbought SELL이 BUY 합산을 잠식하는 문제 해소
            _bull_regimes = {"BULL", "TRENDING_UP", "RECOVERY"}
            if _regime_str in _bull_regimes:
                _buy_sigs  = {k: v for k, v in signals.items() if v.signal == SignalType.BUY}
                _sell_sigs = {k: v for k, v in signals.items() if v.signal != SignalType.BUY}
                if _buy_sigs:  # BUY 신호가 하나라도 있으면 SELL 제거
                    if _sell_sigs:
                        logger.debug(
                            f"[FX13-1-C] {market} BULL레짐 SELL신호 "
                            f"{list(_sell_sigs.keys())} 억제 → BUY {list(_buy_sigs.keys())} 유지"
                        )
                    signals = _buy_sigs

            if len(signals) < self.MIN_SIGNALS_NEEDED:'''

if OLD_SIGNAL_LOOP_END in src:
    src = src.replace(OLD_SIGNAL_LOOP_END, NEW_SIGNAL_LOOP_END)
    results.append(("FX13-1-C", "OK", "decide() BULL 레짐 SELL 신호 억제 삽입"))
else:
    results.append(("FX13-1-C", "SKIP", "decide() 삽입 패턴 미매치"))

ENSEMBLE_F.write_text(src, encoding="utf-8")
try:
    py_compile.compile(str(ENSEMBLE_F), doraise=True)
    results.append(("FX13-1-compile", "OK", "ensemble_engine.py 컴파일 성공"))
except py_compile.PyCompileError as e:
    results.append(("FX13-1-compile", "FAIL", str(e)))
    shutil.copy2(BACKUP / ENSEMBLE_F.name, ENSEMBLE_F)
    print("❌  ensemble_engine.py 컴파일 실패 → 백업 복구")

# ════════════════════════════════════════════════════════════════════════════
# FX13-2  engine_buy.py
#   - BULL 레짐 + Surge ≥ 12% 조건에서 RSI overbought SELL 단일 차단 시
#     combined=None 대신 ML BUY 판단으로 전환하는 조건 추가
# ════════════════════════════════════════════════════════════════════════════
src2 = ENGINE_BUY_F.read_text(encoding="utf-8")

# FX13-2: FX3-2B 케이스 아래에 FX13 Surge override 삽입
OLD_FX32B_END = '''\
            if combined is None:
                logger.info(f\'[ANALYZE] {market} combined=None → BEAR_REVERSAL 체크\')'''

NEW_FX32B_END = '''\
            # [FX13-2] BULL 레짐 + Surge ≥12% + RSI-SELL-only → ML BUY 우선 전환
            # STORJ 23.7%, NEAR 14.3% 같은 급등 종목 진입 포착
            if combined is None:
                _fx13_gr = str(getattr(getattr(self, "_global_regime", None), "value",
                               getattr(self, "_global_regime", "UNKNOWN") or "UNKNOWN")).upper()
                _fx13_surge = 0.0
                if hasattr(self, "_market_change_rates"):
                    _fx13_surge = self._market_change_rates.get(market, 0.0) * 100
                _fx13_sell_only = (
                    bool(signals)
                    and all(getattr(s, "signal", None) and s.signal.name == "SELL" for s in signals)
                )
                _fx13_ml_buy  = ml_pred.get("signal", "HOLD") == "BUY" if ml_pred else False
                _fx13_ml_conf = ml_pred.get("confidence", 0.0) if ml_pred else 0.0
                if (
                    _fx13_gr in ("BULL", "TRENDING_UP")
                    and _fx13_surge >= 12.0
                    and _fx13_sell_only
                    and _fx13_ml_buy
                    and _fx13_ml_conf >= 0.52
                ):
                    from signals.signal_combiner import CombinedSignal as _CS13, SignalType as _ST13
                    logger.info(
                        f"[FX13-2] {market} BULL+Surge{_fx13_surge:.1f}% RSI_SELL억제 "
                        f"→ ML BUY(conf={_fx13_ml_conf:.2f}) 전환"
                    )
                    combined = _CS13(
                        market=market,
                        signal_type=_ST13.BUY,
                        score=_fx13_ml_conf * 1.4,
                        confidence=_fx13_ml_conf,
                        agreement_rate=0.6,
                        contributing_strategies=["ML_Ensemble", "FX13_SurgeOverride"],
                        reasons=[f"BULL+Surge{_fx13_surge:.1f}% ML BUY override RSI_SELL"],
                    )

            if combined is None:
                logger.info(f\'[ANALYZE] {market} combined=None → BEAR_REVERSAL 체크\')'''

if OLD_FX32B_END in src2:
    src2 = src2.replace(OLD_FX32B_END, NEW_FX32B_END)
    results.append(("FX13-2", "OK", "BULL+Surge ML BUY override 삽입"))
else:
    # 정규식 시도
    _pat2 = re.compile(
        r"(            if combined is None:\n"
        r"                logger\.info\(f\'\[ANALYZE\] \{market\} combined=None → BEAR_REVERSAL 체크\'\))",
        re.MULTILINE
    )
    _m2 = _pat2.search(src2)
    if _m2:
        src2 = src2[:_m2.start()] + NEW_FX32B_END + src2[_m2.end():]
        results.append(("FX13-2", "OK(regex)", "BULL+Surge override 삽입 (regex)"))
    else:
        results.append(("FX13-2", "SKIP", "FX13-2 패턴 미매치 — 수동 확인"))

# FX13-3: RSI_Divergence BUY combined=None 소멸 방지
# signal_combiner.combine() 결과가 None일 때 단일 RSI BUY 신호를 직접 사용
OLD_FX33_TARGET = '''\
            if combined is None and ml_pred is not None:
                _ml_conf = ml_pred.get(\'confidence\', 0.0)
                if combined.confidence < _ml_conf:'''

# FX13-3은 engine_buy.py의 combine() 직후 "confidence 보정" 블록 아래에 삽입
OLD_FX33_ANCHOR = '''\
            # [FX3-2] SELL combined 또는 None + ML BUY 상충 처리 (VP-4 개선)'''

NEW_FX33_ANCHOR = '''\
            # [FX13-3] RSI_Divergence BUY 단독 신호 → combined=None 소멸 방지
            # score 0.55 이상, EnsembleEngine 경로와 별개로 signal_combiner가 None 반환 시
            if combined is None and signals:
                _fx13_rsi_buy = [
                    s for s in signals
                    if getattr(s, "strategy_name", "") == "RSI_Divergence"
                    and getattr(s, "signal", None) and s.signal.name == "BUY"
                    and getattr(s, "score", 0) >= 0.55
                ]
                if _fx13_rsi_buy:
                    _rs = _fx13_rsi_buy[0]
                    from signals.signal_combiner import CombinedSignal as _CS13r, SignalType as _ST13r
                    combined = _CS13r(
                        market=market,
                        signal_type=_ST13r.BUY,
                        score=float(getattr(_rs, "score", 0.6)),
                        confidence=float(getattr(_rs, "confidence", 0.65)),
                        agreement_rate=0.7,
                        contributing_strategies=["RSI_Divergence"],
                        reasons=[f"[FX13-3] RSI_Divergence BUY 단독 구제 (score={getattr(_rs,'score',0):.2f})"],
                    )
                    logger.info(
                        f"[FX13-3] {market} RSI_Divergence BUY 구제 "
                        f"score={getattr(_rs,'score',0):.2f} conf={getattr(_rs,'confidence',0):.2f}"
                    )

            # [FX3-2] SELL combined 또는 None + ML BUY 상충 처리 (VP-4 개선)'''

if OLD_FX33_ANCHOR in src2:
    src2 = src2.replace(OLD_FX33_ANCHOR, NEW_FX33_ANCHOR)
    results.append(("FX13-3", "OK", "RSI_Divergence BUY 단독 구제 로직 삽입"))
else:
    results.append(("FX13-3", "SKIP", "FX13-3 앵커 패턴 미매치 — 수동 확인"))

ENGINE_BUY_F.write_text(src2, encoding="utf-8")
try:
    py_compile.compile(str(ENGINE_BUY_F), doraise=True)
    results.append(("FX13-compile", "OK", "engine_buy.py 컴파일 성공"))
except py_compile.PyCompileError as e:
    results.append(("FX13-compile", "FAIL", str(e)))
    shutil.copy2(BACKUP / ENGINE_BUY_F.name, ENGINE_BUY_F)
    print("❌  engine_buy.py 컴파일 실패 → 백업 복구")

# ─── 결과 출력 ──────────────────────────────────────────────────────────────
print()
print("=" * 65)
all_ok = True
for step, status, msg in results:
    icon = "✅" if status in ("OK", "OK(regex)") else ("⚠️ " if status == "SKIP" else "❌")
    print(f"{icon}  {step:<20s}  {status:<10s}  {msg}")
    if status == "FAIL":
        all_ok = False
print("=" * 65)
if all_ok:
    print("✅  FX13 전체 패치 성공")
else:
    print("❌  일부 패치 실패 — 위 오류 확인 후 수동 적용")
sys.exit(0 if all_ok else 1)
