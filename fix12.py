# fix12.py — FX12-1~3 동적 TP/SL 고도화 패치
#
# FX12-1: risk/stop_loss/atr_stop.py
#   GlobalRegime별 TP 배수를 기존 고정값에서 완전 동적 테이블로 교체
#   BULL:       SL×1.10, TP×1.50  → SL여유+TP 크게 확장 (추세 극대화)
#   RECOVERY:   SL×1.00, TP×1.20  → 소폭 확장
#   RANGING:    SL×0.90, TP×0.85  → 횡보: TP 빠르게
#   BEAR_WATCH: SL×0.85, TP×0.80  → 보수적
#   BEAR:       SL×0.70, TP×0.75  → 매우 타이트
#
# FX12-2: core/engine_utils.py
#   calc_exit_plan() TP 배수를 GlobalRegime 인수 기반으로 동적화
#   BULL TP3=5.0→8.0, RANGING TP1=1.5→1.2, BEAR TP1=1.5→1.0
#
# FX12-3: risk/stop_loss/atr_stop.py
#   get_dynamic_levels() 트레일링 SL 수익구간 세분화
#   +2%: BEP(손익분기), +4%: +1.5% 보호, +7%: +3.5%, +12%: +6%
#   BULL 레짐 시 구간 10% 완화 (추세 유지)

import re, shutil, datetime, py_compile, pathlib, sys

BACKUP_DIR = pathlib.Path(
    f"archive/fx12_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
)
TARGETS = [
    "risk/stop_loss/atr_stop.py",
    "core/engine_utils.py",
]

BACKUP_DIR.mkdir(parents=True, exist_ok=True)
for t in TARGETS:
    p   = pathlib.Path(t)
    dst = BACKUP_DIR / pathlib.Path(t).parent
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(t, dst / p.name)
print(f"백업 완료: {BACKUP_DIR}")

results = {}

# ════════════════════════════════════════════════════════════════════════════
# FX12-1  atr_stop.py — GlobalRegime TP 배수 동적 테이블로 교체
# ════════════════════════════════════════════════════════════════════════════
try:
    path_atr = pathlib.Path("risk/stop_loss/atr_stop.py")
    src_atr  = path_atr.read_text(encoding="utf-8")

    # ── 기존 BULL 블록 교체: tp_mult × 1.20 → 동적 테이블 ─────────────────
    OLD_REGIME_BLOCK = '''\
        # GlobalRegime 기반 동적 SL/TP 배수 조정 (Phase 8)
        if global_regime is not None:
            # [FIX-REGIME-CMP] Enum/문자열 모두 처리, 대소문자 무관
            _gr = str(getattr(global_regime, "value", global_regime)).upper()
            if _gr == "BEAR":
                sl_mult *= 0.70   # BEAR: SL 타이트 (손실 최소화)
                tp_mult *= 0.80
                logger.debug(f"[ATR-SL] BEAR 글로벌레짐 → SL 타이트 ({market})")
            elif _gr == "BEAR_WATCH":
                sl_mult *= 0.85   # BEAR_WATCH: SL 약간 타이트
                logger.debug(f"[ATR-SL] BEAR_WATCH 글로벌레짐 → SL 축소 ({market})")
            elif _gr == "RECOVERY":
                sl_mult *= 0.95   # RECOVERY: 약간 보수적
                logger.debug(f"[ATR-SL] RECOVERY 글로벌레짐 → SL 소폭 축소 ({market})")
            elif _gr == "BULL":
                sl_mult *= 1.10   # BULL: SL 여유있게 (추세 추종)
                tp_mult *= 1.20
                logger.debug(f"[ATR-SL] BULL 글로벌레짐 → SL/TP 확장 ({market})")'''

    NEW_REGIME_BLOCK = '''\
        # [FX12-1] GlobalRegime 기반 동적 SL/TP 배수 — 완전 동적 테이블
        # 레짐별 최적 RR 비율 적용
        if global_regime is not None:
            _gr = str(getattr(global_regime, "value", global_regime)).upper()
            # (sl_multiplier, tp_multiplier)
            _REGIME_SL_TP = {
                "BULL":         (1.10, 1.50),  # 강세: SL 여유 + TP 크게 확장
                "TRENDING_UP":  (1.05, 1.35),  # 상승추세: TP 확장
                "RECOVERY":     (1.00, 1.20),  # 회복: 소폭 TP 확장
                "RANGING":      (0.90, 0.85),  # 횡보: TP 빠르게 실현
                "VOLATILE":     (0.80, 0.90),  # 변동성: SL 타이트
                "BEAR_WATCH":   (0.85, 0.80),  # 약세경계: 보수적
                "BEAR":         (0.70, 0.75),  # 약세: 매우 타이트
                "BEAR_REVERSAL":(0.75, 0.85),  # 역발상: 타이트 SL
                "TRENDING_DOWN":(0.70, 0.75),  # 하락: 매우 타이트
                "UNKNOWN":      (1.00, 1.00),  # 불명: 기본값
            }
            _sl_r, _tp_r = _REGIME_SL_TP.get(_gr, (1.00, 1.00))
            sl_mult *= _sl_r
            tp_mult *= _tp_r
            logger.debug(
                f"[ATR-SL][FX12-1] {market} GlobalRegime={_gr} "
                f"SL×{_sl_r:.2f} TP×{_tp_r:.2f} "
                f"→ sl_mult={sl_mult:.2f} tp_mult={tp_mult:.2f}"
            )'''

    if OLD_REGIME_BLOCK in src_atr:
        src_atr = src_atr.replace(OLD_REGIME_BLOCK, NEW_REGIME_BLOCK, 1)
        results["FX12-1-regime"] = "OK  GlobalRegime 동적 테이블 교체"
    else:
        results["FX12-1-regime"] = "SKIP  (기존 블록 불일치 — 수동 확인 필요)"

    # ── FX12-3: get_dynamic_levels() 트레일링 SL 세분화 ────────────────────
    OLD_TRAIL = '''\
        if profit_pct >= 0.10:
            new_sl = entry_price * 1.05
        elif profit_pct >= 0.05:
            new_sl = entry_price * 1.02
        elif profit_pct >= 0.03:
            new_sl = entry_price * 1.001
        else:
            return levels'''

    NEW_TRAIL = '''\
        # [FX12-3] 트레일링 SL 구간 세분화 + BULL 레짐 완화
        _gr_trail = str(getattr(
            global_regime, "value", global_regime or "UNKNOWN"
        )).upper() if global_regime is not None else "UNKNOWN"
        _is_bull_trail = _gr_trail in ("BULL", "TRENDING_UP", "RECOVERY")

        if profit_pct >= 0.12:
            # +12% 이상: +6% 보호 (BULL이면 +4.5%)
            new_sl = entry_price * (1.045 if _is_bull_trail else 1.060)
        elif profit_pct >= 0.07:
            # +7% 이상: +3.5% 보호 (BULL이면 +2.5%)
            new_sl = entry_price * (1.025 if _is_bull_trail else 1.035)
        elif profit_pct >= 0.04:
            # +4% 이상: +1.5% 보호 (BULL이면 BEP+0.5%)
            new_sl = entry_price * (1.005 if _is_bull_trail else 1.015)
        elif profit_pct >= 0.02:
            # +2% 이상: BEP (손익분기) (BULL이면 아직 자유롭게)
            new_sl = entry_price * (0.998 if _is_bull_trail else 1.001)
        else:
            return levels'''

    if OLD_TRAIL in src_atr:
        src_atr = src_atr.replace(OLD_TRAIL, NEW_TRAIL, 1)
        results["FX12-3-trail"] = "OK  트레일링 SL 구간 세분화"
    else:
        results["FX12-3-trail"] = "SKIP  (trail 블록 불일치)"

    path_atr.write_text(src_atr, encoding="utf-8")
    py_compile.compile("risk/stop_loss/atr_stop.py", doraise=True)
    print("컴파일 OK  risk/stop_loss/atr_stop.py")
    results["FX12-1+3"] = "OK"

except Exception as e:
    print(f"FX12-1/3 실패: {e}")
    shutil.copy2(
        BACKUP_DIR / "risk/stop_loss/atr_stop.py",
        "risk/stop_loss/atr_stop.py"
    )
    results["FX12-1+3"] = f"FAIL ({e})"


# ════════════════════════════════════════════════════════════════════════════
# FX12-2  engine_utils.py — calc_exit_plan() GlobalRegime 인수 추가
# ════════════════════════════════════════════════════════════════════════════
try:
    path_eu = pathlib.Path("core/engine_utils.py")
    src_eu  = path_eu.read_text(encoding="utf-8")

    # ── 함수 시그니처에 global_regime 파라미터 추가 ─────────────────────────
    OLD_SIG = "def calc_exit_plan(entry_price: float, atr: float, position_krw: float) -> dict:"
    NEW_SIG = "def calc_exit_plan(entry_price: float, atr: float, position_krw: float, global_regime=None) -> dict:  # [FX12-2]"

    if OLD_SIG in src_eu:
        src_eu = src_eu.replace(OLD_SIG, NEW_SIG, 1)
        results["FX12-2-sig"] = "OK  calc_exit_plan global_regime 파라미터 추가"
    else:
        results["FX12-2-sig"] = "SKIP  (시그니처 불일치)"

    # ── TP 배수 동적화 — 기존 고정값 블록 교체 ──────────────────────────────
    OLD_FIXED = '''\
    sl   = entry_price - atr_mult * 1.5
    tp1  = entry_price + atr_mult * 1.5
    tp2  = entry_price + atr_mult * 3.0
    tp3  = entry_price + atr_mult * 5.0
    trail = 0.015'''

    NEW_FIXED = '''\
    # [FX12-2] GlobalRegime 기반 동적 TP 배수 테이블
    _gr_eu = str(getattr(global_regime, "value", global_regime or "UNKNOWN")).upper() \
        if global_regime is not None else "UNKNOWN"
    _TP_TABLE = {
        "BULL":         (1.5, 3.5, 8.0),   # 강세: TP3 크게 확장
        "TRENDING_UP":  (1.5, 3.0, 6.5),   # 상승: TP3 확장
        "RECOVERY":     (1.5, 2.8, 5.5),   # 회복: 소폭 확장
        "RANGING":      (1.2, 2.0, 3.5),   # 횡보: TP 빠르게
        "VOLATILE":     (1.3, 2.2, 4.0),   # 변동성: 중간
        "BEAR_WATCH":   (1.2, 2.0, 3.5),   # 약세경계: 보수적
        "BEAR":         (1.0, 1.8, 3.0),   # 약세: 매우 보수적
        "BEAR_REVERSAL":(1.2, 2.2, 4.0),   # 역발상: 중간
        "TRENDING_DOWN":(1.0, 1.8, 3.0),   # 하락: 보수적
        "UNKNOWN":      (1.5, 3.0, 5.0),   # 기본값 유지
    }
    _tp1_m, _tp2_m, _tp3_m = _TP_TABLE.get(_gr_eu, (1.5, 3.0, 5.0))

    sl    = entry_price - atr_mult * 1.5
    tp1   = entry_price + atr_mult * _tp1_m
    tp2   = entry_price + atr_mult * _tp2_m
    tp3   = entry_price + atr_mult * _tp3_m
    trail = 0.015'''

    if OLD_FIXED in src_eu:
        src_eu = src_eu.replace(OLD_FIXED, NEW_FIXED, 1)
        results["FX12-2-tp"] = "OK  calc_exit_plan TP 배수 동적화"
    else:
        results["FX12-2-tp"] = "SKIP  (TP 블록 불일치)"

    path_eu.write_text(src_eu, encoding="utf-8")
    py_compile.compile("core/engine_utils.py", doraise=True)
    print("컴파일 OK  core/engine_utils.py")
    results["FX12-2"] = "OK"

except Exception as e:
    print(f"FX12-2 실패: {e}")
    shutil.copy2(
        BACKUP_DIR / "core/engine_utils.py",
        "core/engine_utils.py"
    )
    results["FX12-2"] = f"FAIL ({e})"


# ════════════════════════════════════════════════════════════════════════════
# 결과 출력
# ════════════════════════════════════════════════════════════════════════════
print()
print("─" * 60)
for k, v in results.items():
    icon = "✅" if v.startswith("OK") else ("⚠️ " if v.startswith("SKIP") else "❌")
    print(f"{icon}  {k:<22s} {v}")
print("─" * 60)
print(f"백업: {BACKUP_DIR}")
all_ok = all(v.startswith("OK") or v.startswith("SKIP") for v in results.values())
if all_ok:
    print("✅ FX12 전체 패치 성공")
else:
    print("❌ 일부 실패 — 위 로그 확인")
    sys.exit(1)
