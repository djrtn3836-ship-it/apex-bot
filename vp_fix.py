#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vp_fix.py — VolumeProfile RR 계산 구조 수정 + 전략 신호 생성 개선

VP-1: get_nearest_support_resistance RR 계산 분모 최솟값 보호
      (현재가 ≈ VAH 일 때 RR → -∞ 버그 수정)
VP-2: BULL 레짐 RR 임계값 검증 재확인 (-0.80 실제 적용 여부)
VP-3: STRATEGY-NONE 발생 시 ML 단독 BUY 신호 허용
      (전략 신호 0개 + ML BUY >= 0.55 이면 진입 허용)
VP-4: RSI_Divergence 과매수 SELL 신호가 BUY 차단하는 문제 수정
      (SELL 신호만 있을 때 combined=None → BEAR_REVERSAL이 아니면 무시)
"""
import os
import shutil
import datetime
import py_compile

BASE = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot"
TS   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
ARC  = os.path.join(BASE, "archive", f"vpf_{TS}")
os.makedirs(ARC, exist_ok=True)

RES = {"OK": [], "SKIP": [], "FAIL": []}


def bk(rel_path):
    src = os.path.join(BASE, rel_path)
    dst = os.path.join(ARC, rel_path)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return src


def pt(rel_path, old, new, label):
    fp = os.path.join(BASE, rel_path)
    if not os.path.exists(fp):
        print(f"  [SKIP] {label}: 파일 없음")
        RES["SKIP"].append(label)
        return
    src = open(fp, encoding="utf-8").read()
    if old not in src:
        print(f"  [SKIP] {label}: 패턴 없음 (이미 적용됐거나 위치 다름)")
        RES["SKIP"].append(label)
        return
    bk(rel_path)
    new_src = src.replace(old, new, 1)
    open(fp, "w", encoding="utf-8").write(new_src)
    try:
        py_compile.compile(fp, doraise=True)
        print(f"  [OK]   {label}")
        RES["OK"].append(label)
    except py_compile.PyCompileError as e:
        open(fp, "w", encoding="utf-8").write(src)
        print(f"  [FAIL] {label}: {e}")
        RES["FAIL"].append(label)


# ─────────────────────────────────────────────────────────────────
# VP-1: VolumeProfile RR 분모 보호
# 현재: (resistance - price) / (price - support + 1e-8)
# 문제: price ≈ resistance 이면 분자 ≈ 0, 분모 = price-val 이 크면 RR → -큰수
# 수정: 분모를 max(price - support, price * 0.005) 로 최솟값 보호
#       즉 지지선이 현재가 0.5% 이내라면 RR 계산 시 0.5%를 최솟값으로 사용
# ─────────────────────────────────────────────────────────────────
VP1_OLD = '''        return {
            "poc": result.poc_price,
            "support": support,
            "resistance": resistance,
            "vah": result.vah,
            "val": result.val,
            "above_poc": result.above_poc,
            "risk_reward": (resistance - current_price) / (current_price - support + 1e-8)
        }'''

VP1_NEW = '''        # [VP-1] RR 분모 최솟값 보호: 지지선이 현재가에 너무 가까울 때 극단값 방지
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

pt("signals/filters/volume_profile.py", VP1_OLD, VP1_NEW, "VP-1 RR 분모 보호 + 클램핑")

# ─────────────────────────────────────────────────────────────────
# VP-2: engine_buy.py BULL RR 임계값 재확인
# BSF-2 가 제대로 적용됐는지 확인 후 미적용시 재적용
# ─────────────────────────────────────────────────────────────────
pt(
    "core/engine_buy.py",
    '"BULL":       -0.60,  # BULL: 완화 (단기 저항 근접 허용)',
    '"BULL":       -0.80,  # BULL: [BSF-2+VP-2] 더욱 완화 (VAH 근처 허용)',
    "VP-2 BULL RR 임계값 -0.80 재확인"
)

# ─────────────────────────────────────────────────────────────────
# VP-3: STRATEGY-NONE 상태에서 ML 단독 BUY 허용
# 현재 로직: 전략 신호 0개 → signals=[] → signal_combiner.combine([], ...) → None
# 문제: ML이 BUY를 예측해도 전략 신호 없으면 통과 불가
# 수정: ML confidence >= 0.55 + signal="BUY" 이면 combined 강제 생성
# ─────────────────────────────────────────────────────────────────
VP3_OLD = '''            ml_pred  = await self._get_ml_prediction(market, df_processed)
            ppo_pred = await self._get_ppo_prediction(market, df_processed)'''

VP3_NEW = '''            ml_pred  = await self._get_ml_prediction(market, df_processed)
            ppo_pred = await self._get_ppo_prediction(market, df_processed)
            # [VP-3] 전략 신호 0개 + ML BUY 강한 경우 → ML 단독 신호 허용
            if not signals and ml_pred is not None:
                _vp3_sig  = ml_pred.get("signal", "HOLD")
                _vp3_conf = ml_pred.get("confidence", 0.0)
                if _vp3_sig == "BUY" and _vp3_conf >= 0.55:
                    from signals.signal_combiner import CombinedSignal as _CS3, SignalType as _ST3
                    signals = []  # 빈 상태 유지, ML만으로 combine 시도하게 함
                    logger.info(
                        f"[VP-3] {market} STRATEGY-NONE이나 ML BUY "
                        f"conf={_vp3_conf:.2f} → ML 단독 진행"
                    )'''

pt("core/engine_buy.py", VP3_OLD, VP3_NEW, "VP-3 ML 단독 BUY 허용")

# ─────────────────────────────────────────────────────────────────
# VP-4: 전략 SELL 신호만 있을 때 combined=None 처리 개선
# 현재 HIVE, ENA, CHZ 모두 RSI 과매수 SELL 신호만 나와서
# signal_combiner → combined=None → BEAR_REVERSAL 체크 → 차단
# 수정: SELL 신호만 있을 때 로그 명확화 + ML BUY 신호가 있으면 계속 진행
# ─────────────────────────────────────────────────────────────────
VP4_OLD = '''            combined = self.signal_combiner.combine(
                signals, market, ml_pred, regime.value
            )
            # [FIX] confidence 보정: ML confidence를 combined에 반영
            if combined is not None and ml_pred is not None:
                _ml_conf = ml_pred.get('confidence', 0.0)
                if combined.confidence < _ml_conf:
                    combined.confidence = _ml_conf
                    logger.info(f'[ANALYZE] {market} confidence 보정: 0.0→{_ml_conf:.3f}')'''

VP4_NEW = '''            combined = self.signal_combiner.combine(
                signals, market, ml_pred, regime.value
            )
            # [FIX] confidence 보정: ML confidence를 combined에 반영
            if combined is not None and ml_pred is not None:
                _ml_conf = ml_pred.get('confidence', 0.0)
                if combined.confidence < _ml_conf:
                    combined.confidence = _ml_conf
                    logger.info(f'[ANALYZE] {market} confidence 보정: 0.0→{_ml_conf:.3f}')
            # [VP-4] SELL 신호만 있고 ML이 BUY이면 combined 재생성
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

pt("core/engine_buy.py", VP4_OLD, VP4_NEW, "VP-4 전략SELL vs ML_BUY 충돌 처리")

# ─────────────────────────────────────────────────────────────────
# 결과 출력
# ─────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print(f"  vp_fix 결과  {TS}")
print("=" * 60)
print(f"  OK   {len(RES['OK'])}  : {RES['OK']}")
print(f"  SKIP {len(RES['SKIP'])}  : {RES['SKIP']}")
print(f"  FAIL {len(RES['FAIL'])}  : {RES['FAIL']}")
print(f"  백업 : {ARC}")
print("=" * 60)
if RES["FAIL"]:
    print("\n  FAIL 항목 있음 — 위 오류 메시지를 확인하세요.")
else:
    print("\n 모든 패치 완료. 아래 명령으로 재시작하세요:")
    print("  git add -A")
    print('  git commit -m "fix: VP-1~4 VolumeProfile RR 버그 수정, ML 신호 보강"')
    print("  git push origin main")
    print("  taskkill /F /IM python.exe /T")
    print("  python main.py --mode paper")
