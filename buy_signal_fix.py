#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
buy_signal_fix.py
BUY 신호 차단 7개 레이어 수정 패치

패치 목록:
  BSF-1: MTFGate BULL 임계값 -0.10 → -0.30 (1d/4h 하향 허용 확대)
  BSF-2: VolumeProfile BULL RR 임계값 -0.60 → -0.80 (VAH 근처 허용)
  BSF-3: RegimeDetector TRENDING_DOWN → RANGING 완화 (GlobalBULL 시)
  BSF-4: BEAR_REVERSAL Fear&Greed 조건 완화 (≤25 → ≤35)
  BSF-5: SignalCombiner ORDER_BLOCK_SMC 가중치 0.3 → 1.0 복원
  BSF-6: engine_buy TRENDING_DOWN 차단 완화 (GlobalBULL 포함)
  BSF-7: optimized_params.json weight_boost 0.909 → 1.0 재확인
"""
import os
import shutil
import datetime
import py_compile
import json

BASE = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot"
TS   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
ARC  = os.path.join(BASE, "archive", f"bsf_{TS}")
os.makedirs(ARC, exist_ok=True)

RES = {"OK": [], "SKIP": [], "FAIL": []}


def bk(rel_path: str) -> str:
    src = os.path.join(BASE, rel_path)
    dst = os.path.join(ARC, rel_path)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return src


def pt(rel_path: str, old: str, new: str, label: str) -> None:
    fp = os.path.join(BASE, rel_path)
    if not os.path.exists(fp):
        print(f"  [SKIP] {label}: 파일 없음 ({rel_path})")
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


# ─────────────────────────────────────────────────────────
# BSF-1: MTFGate BULL 임계값 완화 -0.10 → -0.30
# ─────────────────────────────────────────────────────────
pt(
    "signals/mtf_gate.py",
    "GATE_THRESHOLD_BULL    = -0.10   # BULL: 약간의 역방향도 허용",
    "GATE_THRESHOLD_BULL    = -0.30   # BULL: [BSF-1] 역방향 허용 확대",
    "BSF-1 MTFGate BULL 임계값 -0.30"
)

# ─────────────────────────────────────────────────────────
# BSF-2: VolumeProfile BULL RR 임계값 -0.60 → -0.80
# ─────────────────────────────────────────────────────────
pt(
    "core/engine_buy.py",
    '"BULL":       -0.60,  # BULL: 완화 (단기 저항 근접 허용)',
    '"BULL":       -0.80,  # BULL: [BSF-2] 더욱 완화 (VAH 근처 허용)',
    "BSF-2 VolumeProfile BULL RR -0.80"
)

# ─────────────────────────────────────────────────────────
# BSF-3: RegimeDetector TRENDING_DOWN 완화
# GlobalBULL + EMA200 근접(-15% 이내)이면 RANGING으로 처리
# ─────────────────────────────────────────────────────────
OLD_CLASSIFY_END = """        # 중립: EMA200 기준
        return MarketRegime.TRENDING_UP if price > ema200 else MarketRegime.TRENDING_DOWN"""

NEW_CLASSIFY_END = """        # 중립: EMA200 기준
        # [BSF-3] EMA200 근접(-15% 이내)이면 TRENDING_DOWN 대신 RANGING
        if price <= ema200:
            _ema200_diff = (price - ema200) / (ema200 + 1e-9)
            if _ema200_diff > -0.15:
                return MarketRegime.RANGING
        return MarketRegime.TRENDING_UP if price > ema200 else MarketRegime.TRENDING_DOWN"""

pt(
    "signals/filters/regime_detector.py",
    OLD_CLASSIFY_END,
    NEW_CLASSIFY_END,
    "BSF-3 RegimeDetector TRENDING_DOWN→RANGING 완화"
)

# ─────────────────────────────────────────────────────────
# BSF-4: BEAR_REVERSAL Fear&Greed 조건 완화 ≤25 → ≤40
# ─────────────────────────────────────────────────────────
pt(
    "signals/filters/regime_detector.py",
    "        if fear_greed_index is not None and fear_greed_index <= 25:",
    "        if fear_greed_index is not None and fear_greed_index <= 40:  # [BSF-4]",
    "BSF-4 BEAR_REVERSAL FearGreed 조건 ≤40"
)

# ─────────────────────────────────────────────────────────
# BSF-5: SignalCombiner ORDER_BLOCK_SMC 가중치 0.3 → 1.0
# 기존 0.3은 백테스트 기준으로 하향되었으나
# 현재 BULL 레짐에서는 정상 가중치 필요
# ─────────────────────────────────────────────────────────
pt(
    "signals/signal_combiner.py",
    "        StrategyKey.ORDER_BLOCK_SMC:   0.3,   # 백테스트 -4.7% → 하향",
    "        StrategyKey.ORDER_BLOCK_SMC:   1.0,   # [BSF-5] BULL 레짐 복원",
    "BSF-5 OrderBlock 가중치 0.3→1.0"
)

# ─────────────────────────────────────────────────────────
# BSF-6: engine_buy TRENDING_DOWN 차단에 GlobalBULL 조건 추가
# 현재 코드: BULL/RECOVERY 일 때만 완화
# 수정: BULL 레짐에서는 RANGING과 동일하게 처리
# ─────────────────────────────────────────────────────────
pt(
    "core/engine_buy.py",
    '            if regime == MarketRegime.TRENDING_DOWN and _gr_vp3 not in ("BULL", "RECOVERY"):',
    '            if regime == MarketRegime.TRENDING_DOWN and _gr_vp3 not in ("BULL", "RECOVERY", "UNKNOWN"):  # [BSF-6]',
    "BSF-6 TRENDING_DOWN 차단 완화 (UNKNOWN도 허용)"
)

# ─────────────────────────────────────────────────────────
# BSF-7: optimized_params.json weight_boost 재확인 및 수정
# ─────────────────────────────────────────────────────────
_params_path = os.path.join(BASE, "config", "optimized_params.json")
if os.path.exists(_params_path):
    try:
        with open(_params_path, encoding="utf-8") as _f:
            _params = json.load(_f)
        _changed = False
        _strats  = _params.get("strategies", {})
        for _sname, _sinfo in _strats.items():
            if isinstance(_sinfo, dict):
                _wb = _sinfo.get("weight_boost", 1.0)
                if abs(_wb - 0.9091) < 0.001 or abs(_wb - 0.909) < 0.001:
                    _strats[_sname]["weight_boost"] = 1.0
                    _changed = True
                    print(f"  [BSF-7] {_sname}: weight_boost {_wb:.4f} → 1.0")
        if _changed:
            _params_bk = _params_path + f".bak_{TS}"
            shutil.copy2(_params_path, _params_bk)
            with open(_params_path, "w", encoding="utf-8") as _f:
                json.dump(_params, _f, indent=2, ensure_ascii=False)
            print(f"  [OK]   BSF-7 weight_boost 수정 완료")
            RES["OK"].append("BSF-7 weight_boost 1.0")
        else:
            print(f"  [SKIP] BSF-7: weight_boost 이미 1.0")
            RES["SKIP"].append("BSF-7 weight_boost")
    except Exception as _e:
        print(f"  [FAIL] BSF-7: {_e}")
        RES["FAIL"].append("BSF-7")
else:
    print(f"  [SKIP] BSF-7: optimized_params.json 없음")
    RES["SKIP"].append("BSF-7")

# ─────────────────────────────────────────────────────────
# 결과 출력
# ─────────────────────────────────────────────────────────
print()
print("=" * 60)
print(f"  buy_signal_fix 결과  {TS}")
print("=" * 60)
print(f"  OK   {len(RES['OK'])}  : {RES['OK']}")
print(f"  SKIP {len(RES['SKIP'])}  : {RES['SKIP']}")
print(f"  FAIL {len(RES['FAIL'])}  : {RES['FAIL']}")
print(f"  백업 : {ARC}")
print("=" * 60)
if RES["FAIL"]:
    print("\n⚠ FAIL 항목이 있습니다. 위 오류 메시지를 확인하세요.")
else:
    print("\n✅ 모든 패치 완료. 아래 명령으로 재시작하세요:")
    print("  git add -A")
    print('  git commit -m "fix: BSF-1~7 BUY 신호 차단 레이어 완화 패치"')
    print("  git push origin main")
    print("  taskkill /F /IM python.exe /T")
    print("  python main.py --mode paper")
