#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fix15.py — FX15-1~2 패치
FX15-1: v2_layer.py
  - BULL/TRENDING_UP/RECOVERY 레짐에서 V2Layer 거부 conf 임계값 0.65 → 0.60 완화
  - decide()에서 should_enter=False + BULL 레짐 시 confidence boost (+0.08) 적용
    → Ensemble 점수 경계값 0.65 근처에서 BULL 레짐 혜택 부여
FX15-2: signals/mtf_gate.py
  - RSI 극과매도(≤22) 종목 MTFGate softfail 처리
    (1h DOWN이라도 RSI 바닥권이면 역방향 매수 허용)
"""
from __future__ import annotations
import shutil, py_compile, pathlib, sys
from datetime import datetime

ROOT       = pathlib.Path(__file__).parent
V2LAYER_F  = ROOT / "strategies/v2/v2_layer.py"
MTFGATE_F  = ROOT / "signals/mtf_gate.py"
ENSEMBLE_F = ROOT / "strategies/v2/ensemble_engine.py"

TS     = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP = ROOT / f"archive/fx15_{TS}"
BACKUP.mkdir(parents=True, exist_ok=True)
for _f in [V2LAYER_F, MTFGATE_F, ENSEMBLE_F]:
    if _f.exists():
        shutil.copy2(_f, BACKUP / _f.name)
print(f"✅  백업 완료: {BACKUP}")

results = []

# ════════════════════════════════════════════════════════════════════════
# FX15-1-A  v2_layer.py — V2 거부 conf 임계값 BULL 레짐 0.65 → 0.60
# 현재 코드:
#   elif not decision.should_enter and decision.confidence >= 0.65:
# ════════════════════════════════════════════════════════════════════════
src_v2 = V2LAYER_F.read_text(encoding="utf-8")

OLD_V2_REFUSE = '''\
            elif not decision.should_enter and decision.confidence >= 0.65:
                _logger.info(f"[V2Layer] {market} v2 거부 conf={decision.confidence:.2f}")
                return False, combined_conf, 1.0'''

NEW_V2_REFUSE = '''\
            elif not decision.should_enter and decision.confidence >= 0.65:
                # [FX15-1-A] BULL/TRENDING_UP/RECOVERY 레짐에서 임계값 0.65→0.60 완화
                # GlobalRegime은 fallback_regime 인자로 주입됨
                _fx15_bull_r = str(fallback_regime).upper() in ("BULL", "TRENDING_UP", "RECOVERY")
                _fx15_refuse_thr = 0.60 if _fx15_bull_r else 0.65
                if decision.confidence >= _fx15_refuse_thr:
                    _logger.info(
                        f"[V2Layer] {market} v2 거부 conf={decision.confidence:.2f} "
                        f"(thr={_fx15_refuse_thr:.2f} regime={fallback_regime})"
                    )
                    return False, combined_conf, 1.0
                else:
                    # [FX15-1-A] BULL 레짐 conf 0.60~0.65 구간: 거부 취소 → v1 폴백
                    _logger.info(
                        f"[V2Layer] {market} BULL레짐 v2 거부 완화 "
                        f"conf={decision.confidence:.2f} < thr={_fx15_refuse_thr:.2f} → v1 폴백"
                    )
                    return True, v1_confidence, 1.0'''

if OLD_V2_REFUSE in src_v2:
    src_v2 = src_v2.replace(OLD_V2_REFUSE, NEW_V2_REFUSE)
    results.append(("FX15-1-A", "OK", "V2 거부 BULL 임계값 0.65→0.60 완화"))
else:
    results.append(("FX15-1-A", "FAIL", "V2 거부 패턴 미매치 — 수동 확인"))

# FX15-1-B: Ensemble decide() should_enter=False지만 BULL 레짐 conf boost
# → ensemble_engine.py 의 decide() 반환 직전에 BULL boost 삽입
src_ens = ENSEMBLE_F.read_text(encoding="utf-8")

OLD_ENS_RETURN = '''\
            if should_enter:
                logger.info(
                    f"[Ensemble] ✅ {market} 진입결정 | {reasoning} | "
                    f"사이즈배수={size_mult:.1f}"
                )
            else:
                logger.debug(
                    f"[Ensemble] ❌ {market} 진입거부 | {reasoning}"
                )'''

NEW_ENS_RETURN = '''\
            # [FX15-1-B] BULL/TRENDING_UP 레짐 경계값 보정
            # normalized 0.47~0.55 구간에서 BULL 레짐이면 +0.08 보정
            _fx15_bull_regime = str(_regime_str).upper() in ("BULL", "TRENDING_UP", "RECOVERY")
            if _fx15_bull_regime and not should_enter and normalized >= 0.47:
                _old_norm = normalized
                normalized = min(normalized + 0.08, 0.85)
                size_mult  = 1.0 if normalized >= 0.65 else 0.8
                should_enter = normalized >= self.ENTRY_THRESHOLD
                logger.debug(
                    f"[FX15-1-B] {market} BULL레짐 score 보정 "
                    f"{_old_norm:.3f} → {normalized:.3f} enter={should_enter}"
                )

            if should_enter:
                logger.info(
                    f"[Ensemble] ✅ {market} 진입결정 | {reasoning} | "
                    f"사이즈배수={size_mult:.1f}"
                )
            else:
                logger.debug(
                    f"[Ensemble] ❌ {market} 진입거부 | {reasoning}"
                )'''

if OLD_ENS_RETURN in src_ens:
    src_ens = src_ens.replace(OLD_ENS_RETURN, NEW_ENS_RETURN)
    results.append(("FX15-1-B", "OK", "Ensemble BULL 레짐 score +0.08 보정 삽입"))
else:
    results.append(("FX15-1-B", "SKIP", "Ensemble 패턴 미매치"))

ENSEMBLE_F.write_text(src_ens, encoding="utf-8")
try:
    py_compile.compile(str(ENSEMBLE_F), doraise=True)
    results.append(("FX15-1-B-compile", "OK", "ensemble_engine.py 컴파일 성공"))
except py_compile.PyCompileError as e:
    results.append(("FX15-1-B-compile", "FAIL", str(e)))
    shutil.copy2(BACKUP / ENSEMBLE_F.name, ENSEMBLE_F)
    print("❌  ensemble_engine.py 컴파일 실패 → 백업 복구")

V2LAYER_F.write_text(src_v2, encoding="utf-8")
try:
    py_compile.compile(str(V2LAYER_F), doraise=True)
    results.append(("FX15-1-compile", "OK", "v2_layer.py 컴파일 성공"))
except py_compile.PyCompileError as e:
    results.append(("FX15-1-compile", "FAIL", str(e)))
    shutil.copy2(BACKUP / V2LAYER_F.name, V2LAYER_F)
    print("❌  v2_layer.py 컴파일 실패 → 백업 복구")

# ════════════════════════════════════════════════════════════════════════
# FX15-2  signals/mtf_gate.py
# MTFSignalMerger 차단이 근본 원인이나, MTFGate에도
# RSI ≤ 22 극과매도 종목 softfail 예외 추가
# → check() 진입부에 RSI 기반 override 삽입
# ════════════════════════════════════════════════════════════════════════
src_mtf = MTFGATE_F.read_text(encoding="utf-8")

OLD_MTF_CHECK_TOP = '''\
        directions: Dict[str, int] = {}
        total_weight = 0.0
        weighted_dir = 0.0
        softfail_tfs = []'''

NEW_MTF_CHECK_TOP = '''\
        # [FX15-2] RSI 극과매도(≤22) 종목 → 1h DOWN이어도 softfail 허용
        # HIVE RSI 20.x 같은 극단적 oversold 진입 포착
        _fx15_rsi_oversold = False
        _fx15_1h_df = tf_data.get("1h")
        if _fx15_1h_df is not None and len(_fx15_1h_df) >= 14:
            try:
                _rsi_col = None
                for _rc in ("rsi", "RSI", "rsi_14"):
                    if _rc in _fx15_1h_df.columns:
                        _rsi_col = _rc
                        break
                if _rsi_col:
                    _fx15_rsi_val = float(_fx15_1h_df[_rsi_col].iloc[-1])
                else:
                    # 간이 RSI 계산
                    _c15 = _fx15_1h_df["close"].values.astype(float)[-15:]
                    _d15 = import_numpy_diff(_c15) if False else __import__("numpy").diff(_c15)
                    _g15 = float(__import__("numpy").mean(_d15[_d15 > 0])) if __import__("numpy").any(_d15 > 0) else 0
                    _l15 = float(__import__("numpy").mean(-_d15[_d15 < 0])) if __import__("numpy").any(_d15 < 0) else 1e-9
                    _fx15_rsi_val = 100 - 100 / (1 + _g15 / (_l15 + 1e-9))
                if _fx15_rsi_val <= 22.0 and signal_direction == 1:
                    _fx15_rsi_oversold = True
                    logger.info(
                        f"[FX15-2] RSI 극과매도 {_fx15_rsi_val:.1f} ≤ 22 "
                        f"→ MTFGate softfail 허용 (BUY 방향)"
                    )
            except Exception as _fx15_e:
                logger.debug(f"[FX15-2] RSI 체크 오류: {_fx15_e}")

        if _fx15_rsi_oversold:
            return MTFGateResult(
                allowed=True,
                score=0.0,
                reason=f"[FX15-2] RSI 극과매도 softfail → 진입 허용",
                tf_directions={},
                is_softfail=True,
            )

        directions: Dict[str, int] = {}
        total_weight = 0.0
        weighted_dir = 0.0
        softfail_tfs = []'''

if OLD_MTF_CHECK_TOP in src_mtf:
    src_mtf = src_mtf.replace(OLD_MTF_CHECK_TOP, NEW_MTF_CHECK_TOP)
    results.append(("FX15-2", "OK", "MTFGate RSI≤22 극과매도 softfail 삽입"))
else:
    results.append(("FX15-2", "FAIL", "MTFGate 패턴 미매치 — 수동 확인"))

MTFGATE_F.write_text(src_mtf, encoding="utf-8")
try:
    py_compile.compile(str(MTFGATE_F), doraise=True)
    results.append(("FX15-2-compile", "OK", "mtf_gate.py 컴파일 성공"))
except py_compile.PyCompileError as e:
    results.append(("FX15-2-compile", "FAIL", str(e)))
    shutil.copy2(BACKUP / MTFGATE_F.name, MTFGATE_F)
    print("❌  mtf_gate.py 컴파일 실패 → 백업 복구")

# ─── 결과 출력 ─────────────────────────────────────────────────────────
print()
print("=" * 68)
all_ok = True
for step, status, msg in results:
    icon = "✅" if status == "OK" else ("⚠️ " if status == "SKIP" else "❌")
    print(f"{icon}  {step:<22s}  {status:<10s}  {msg}")
    if status == "FAIL":
        all_ok = False
print("=" * 68)
print("✅  FX15 전체 패치 성공" if all_ok else "❌  일부 실패 — 위 오류 확인")
sys.exit(0 if all_ok else 1)
