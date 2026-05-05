#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix3.py — 잔여 BUY 차단 최종 수정

FX3-1: VolumeProfile BULL RR 임계값 -0.80 → -0.95 (BTC/ETH 포함)
FX3-2: VP-4 로직 수정 — combined=None 조건 대신
       combined.signal_type == SELL 일 때 ML BUY 우선 처리
FX3-3: AVAX/ZIL 등 지지=저항 동일 코인 RR=-1.00 처리
       (지지=저항인 경우 RR을 0.0으로 강제)
FX3-4: FASTTRACK 코인 분석 파이프라인 진입 허용
       (SURGE_FASTENTRY 우회가 아닌 일반 파이프라인으로 라우팅)
"""
import os, shutil, datetime, py_compile

BASE = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot"
TS   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
ARC  = os.path.join(BASE, "archive", f"fx3_{TS}")
os.makedirs(ARC, exist_ok=True)
RES  = {"OK": [], "SKIP": [], "FAIL": []}

def bk(rel):
    src = os.path.join(BASE, rel)
    dst = os.path.join(ARC, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)

def pt(rel, old, new, label):
    fp = os.path.join(BASE, rel)
    if not os.path.exists(fp):
        print(f"  [SKIP] {label}: 파일없음"); RES["SKIP"].append(label); return
    src = open(fp, encoding="utf-8").read()
    if old not in src:
        print(f"  [SKIP] {label}: 패턴없음"); RES["SKIP"].append(label); return
    bk(rel)
    open(fp, "w", encoding="utf-8").write(src.replace(old, new, 1))
    try:
        py_compile.compile(fp, doraise=True)
        print(f"  [OK]   {label}"); RES["OK"].append(label)
    except py_compile.PyCompileError as e:
        open(fp, "w", encoding="utf-8").write(src)
        print(f"  [FAIL] {label}: {e}"); RES["FAIL"].append(label)

# ─────────────────────────────────────────────────────────────────
# FX3-1: BULL RR 임계값 -0.95 (BTC -0.87, ETH -0.68 모두 통과)
# ─────────────────────────────────────────────────────────────────
pt(
    "core/engine_buy.py",
    '"BULL":       -0.80,  # BULL: [BSF-2+VP-2] 더욱 완화 (VAH 근처 허용)',
    '"BULL":       -0.95,  # BULL: [FX3-1] 최대 완화 (BTC/ETH VAH 포함)',
    "FX3-1 BULL RR 임계값 -0.95"
)

# ─────────────────────────────────────────────────────────────────
# FX3-2: VP-4 수정 — combined SELL 타입일 때 ML BUY 우선 처리
# 현재 VP-4는 combined=None 조건인데
# RSI SELL 신호가 threshold를 초과해 SELL combined가 생성되는 경우를 처리
# ─────────────────────────────────────────────────────────────────
FX32_OLD = '''            # [VP-4] SELL 신호만 있고 ML이 BUY이면 combined 재생성
            if combined is None and signals and ml_pred is not None:
                _vp4_sell_only = all(
                    getattr(s, "signal", None) and s.signal.name == "SELL"
                    for s in signals
                )
                _vp4_ml_buy = ml_pred.get("signal", "HOLD") == "BUY"
                _vp4_ml_conf = ml_pred.get("confidence", 0.0)
                if _vp4_sell_only and _vp4_ml_buy and _vp4_ml_conf >= 0.58:
                    from signals.signal_combiner import CombinedSignal as _CS4, SignalType as _ST4
                    combined = _CS4(
                        market=market,
                        signal_type=_ST4.BUY,
                        score=_vp4_ml_conf * 1.5,
                        confidence=_vp4_ml_conf,
                        agreement_rate=0.5,
                        contributing_strategies=["ML_Ensemble"],
                        reasons=[f"ML BUY override (전략 SELL 신호 상충, ML conf={_vp4_ml_conf:.2f})"],
                    )
                    logger.info(
                        f"[VP-4] {market} 전략SELL vs ML_BUY 상충 → "
                        f"ML 우선(conf={_vp4_ml_conf:.2f}) BUY 진행"
                    )'''

FX32_NEW = '''            # [FX3-2] SELL combined 또는 None + ML BUY 상충 처리 (VP-4 개선)
            from signals.signal_combiner import SignalType as _ST4fx
            _fx32_ml_buy  = ml_pred.get("signal", "HOLD") == "BUY" if ml_pred else False
            _fx32_ml_conf = ml_pred.get("confidence", 0.0) if ml_pred else 0.0
            _fx32_sell_only_signals = (
                bool(signals)
                and all(getattr(s, "signal", None) and s.signal.name == "SELL" for s in signals)
            )
            # Case A: combined=SELL 인데 ML=BUY (RSI과매수 vs ML 상충)
            if (
                combined is not None
                and combined.signal_type == _ST4fx.SELL
                and _fx32_ml_buy
                and _fx32_ml_conf >= 0.58
                and _fx32_sell_only_signals
            ):
                from signals.signal_combiner import CombinedSignal as _CS4fx
                logger.info(
                    f"[FX3-2A] {market} RSI_SELL vs ML_BUY 상충 "
                    f"→ ML 우선(conf={_fx32_ml_conf:.2f}) BUY 전환"
                )
                combined = _CS4fx(
                    market=market,
                    signal_type=_ST4fx.BUY,
                    score=_fx32_ml_conf * 1.5,
                    confidence=_fx32_ml_conf,
                    agreement_rate=0.5,
                    contributing_strategies=["ML_Ensemble"],
                    reasons=[f"ML BUY override RSI_SELL (conf={_fx32_ml_conf:.2f})"],
                )
            # Case B: combined=None + SELL 신호만 + ML BUY (VP-4 원래 케이스)
            elif (
                combined is None
                and _fx32_sell_only_signals
                and _fx32_ml_buy
                and _fx32_ml_conf >= 0.58
            ):
                from signals.signal_combiner import CombinedSignal as _CS4fx
                logger.info(
                    f"[FX3-2B] {market} 전략SELL vs ML_BUY 상충(None) "
                    f"→ ML 우선(conf={_fx32_ml_conf:.2f}) BUY 진행"
                )
                combined = _CS4fx(
                    market=market,
                    signal_type=_ST4fx.BUY,
                    score=_fx32_ml_conf * 1.5,
                    confidence=_fx32_ml_conf,
                    agreement_rate=0.5,
                    contributing_strategies=["ML_Ensemble"],
                    reasons=[f"ML BUY override (전략SELL 상충, conf={_fx32_ml_conf:.2f})"],
                )'''

pt("core/engine_buy.py", FX32_OLD, FX32_NEW, "FX3-2 VP-4 개선 (SELL combined 포함)")

# ─────────────────────────────────────────────────────────────────
# FX3-3: VolumeProfile 지지=저항 동일 시 RR=0.0 처리
# AVAX: 저항=13,763 지지=13,763 → 분자=0, RR=-(price-val)/(tiny) → -큰수
# 클램핑이 -2.0이면 BULL 임계 -0.95보다 낮아 여전히 차단됨
# 해결: 저항 ≤ 지지+1원 이면 RR=0.0 (포지션 중립, 통과)
# ─────────────────────────────────────────────────────────────────
FX33_OLD = '''        # [VP-1] RR 분모 최솟값 보호: 지지선이 현재가에 너무 가까울 때 극단값 방지
        _rr_numerator   = resistance - current_price
        _rr_denominator = max(current_price - support, current_price * 0.005)  # 최솟값 0.5%
        _rr_value       = _rr_numerator / (_rr_denominator + 1e-8)
        # [VP-1] RR 상한/하한 클램핑: -2.0 ~ +50.0 범위로 제한
        _rr_value = max(-2.0, min(50.0, _rr_value))
        return {
            "poc": result.poc_price,
            "support": support,
            "resistance": resistance,
            "vah": result.vah,
            "val": result.val,
            "above_poc": result.above_poc,
            "risk_reward": _rr_value,
        }'''

FX33_NEW = '''        # [VP-1+FX3-3] RR 계산 개선
        # FX3-3: 저항 ≤ 지지 + 1원 (HVN 없어서 동일값) → RR=0.0 처리
        if resistance <= support + 1.0:
            _rr_value = 0.0
        else:
            _rr_numerator   = resistance - current_price
            _rr_denominator = max(current_price - support, current_price * 0.005)
            _rr_value       = _rr_numerator / (_rr_denominator + 1e-8)
            _rr_value = max(-2.0, min(50.0, _rr_value))
        return {
            "poc": result.poc_price,
            "support": support,
            "resistance": resistance,
            "vah": result.vah,
            "val": result.val,
            "above_poc": result.above_poc,
            "risk_reward": _rr_value,
        }'''

pt("signals/filters/volume_profile.py", FX33_OLD, FX33_NEW, "FX3-3 지지=저항 시 RR=0.0")

# ─────────────────────────────────────────────────────────────────
# FX3-4: FASTTRACK 코인 일반 분석 파이프라인 진입 허용
# 현재: SURGE_FASTENTRY 비활성화(PHASE2) → FASTTRACK 코인도 return
# 문제: is_surge=True 코인이 SURGE_DISABLED 로그 후 return됨
# 수정: SURGE_DISABLED 후 return 제거 → 일반 파이프라인 계속 진행
# ─────────────────────────────────────────────────────────────────
FX34_OLD = '''                # [PHASE2] SURGE_FASTENTRY 영구 비활성화
                # 사유: 누적 손실 -41.02% (326 trades, WR 46%)
                logger.debug(f"[SURGE-DISABLED] {market} → 스킵")
                return'''

FX34_NEW = '''                # [FX3-4] SURGE_FASTENTRY 비활성화 후 일반 파이프라인으로 계속 진행
                # (기존: return → 완전 스킵 / 변경: 아래 일반 분석 파이프라인 진행)
                logger.debug(f"[SURGE-PIPELINE] {market} → 일반 파이프라인 진행")'''

pt("core/engine_buy.py", FX34_OLD, FX34_NEW, "FX3-4 FASTTRACK 일반 파이프라인 진입")

# ─────────────────────────────────────────────────────────────────
# 결과 출력
# ─────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print(f"  fix3 결과  {TS}")
print("=" * 60)
print(f"  OK   {len(RES['OK'])}  : {RES['OK']}")
print(f"  SKIP {len(RES['SKIP'])}  : {RES['SKIP']}")
print(f"  FAIL {len(RES['FAIL'])}  : {RES['FAIL']}")
print(f"  백업 : {ARC}")
print("=" * 60)
if RES["FAIL"]:
    print("\n  FAIL 항목 있음 — 오류 메시지 확인하세요.")
else:
    print("\n 패치 완료. 실행하세요:")
    print("  git add -A")
    print('  git commit -m "fix: FX3-1~4 RR 임계 완화, VP-4 개선, FASTTRACK 파이프라인"')
    print("  git push origin main")
    print("  taskkill /F /IM python.exe /T")
    print("  python main.py --mode paper")
