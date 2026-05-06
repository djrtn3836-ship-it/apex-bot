# fix9.py — FX9-1~3 패치
# FX9-1: position_sizer.py MIN_RISK_PCT 0.05→0.02
# FX9-2: ensemble_engine.py BASE_WEIGHTS 초기화 제곱 버그 수정
# FX9-3: engine_sell.py 익절 쿨다운을 _profit_cooldown 분리
#         engine_buy.py _profit_cooldown 체크 추가

import os, shutil, py_compile, datetime, pathlib

ROOT      = pathlib.Path(".")
ARCHIVE   = ROOT / "archive" / f"fx9_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
ARCHIVE.mkdir(parents=True, exist_ok=True)

TARGETS = {
    "risk/position_sizer.py":             ROOT / "risk/position_sizer.py",
    "strategies/v2/ensemble_engine.py":   ROOT / "strategies/v2/ensemble_engine.py",
    "core/engine_sell.py":                ROOT / "core/engine_sell.py",
    "core/engine_buy.py":                 ROOT / "core/engine_buy.py",
}

# ── 백업 ─────────────────────────────────────────────────────────────
for rel, path in TARGETS.items():
    if path.exists():
        dst = ARCHIVE / pathlib.Path(rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
print(f"백업 완료: {ARCHIVE}")

results = {}

# ════════════════════════════════════════════════════════════════
# FX9-1 — risk/position_sizer.py
#   MIN_RISK_PCT = 0.05  →  MIN_RISK_PCT = 0.02
# ════════════════════════════════════════════════════════════════
p1 = TARGETS["risk/position_sizer.py"]
src1 = p1.read_text(encoding="utf-8")

OLD1 = "    MIN_RISK_PCT  = 0.05"
NEW1 = "    MIN_RISK_PCT  = 0.02  # [FX9-1] 최소 포지션 5%→2% (저신뢰도 과잉 투입 방지)"

if OLD1 in src1:
    src1 = src1.replace(OLD1, NEW1, 1)
    p1.write_text(src1, encoding="utf-8")
    results["FX9-1"] = "OK   MIN_RISK_PCT 0.05→0.02"
else:
    results["FX9-1"] = "SKIP 패턴없음"

# ════════════════════════════════════════════════════════════════
# FX9-2 — strategies/v2/ensemble_engine.py
#   BASE_WEIGHTS 초기화 제곱 버그 수정
#   기존: k: round(self.BASE_WEIGHTS.get(k,v) * v, 3) for k,v in {merged}.items()
#   변경: config boost를 명시적으로 곱하는 방식으로 재작성
# ════════════════════════════════════════════════════════════════
p2 = TARGETS["strategies/v2/ensemble_engine.py"]
src2 = p2.read_text(encoding="utf-8")

# 제거할 원래 블록 (들여쓰기 포함, 정확히 일치해야 함)
OLD2 = (
    "        # [FP3-PATCH] config boost를 절대값이 아닌 배율로 적용\n"
    "        # 예: MACD_Cross base=1.2, boost=1.3 → 1.2*1.3=1.56\n"
    "        self.BASE_WEIGHTS = {\n"
    "            k: round(self.BASE_WEIGHTS.get(k, v) * v, 3)\n"
    "            for k, v in {**self.BASE_WEIGHTS, **{\n"
    "                k2: _cfg_boosts.get(k2, 1.0)\n"
    "                for k2 in self.BASE_WEIGHTS\n"
    "            }}.items()\n"
    "        }"
)

NEW2 = (
    "        # [FX9-2] BASE_WEIGHTS 초기화 버그 수정\n"
    "        # config boost를 명시적으로 곱함 (실패 시 원래 기본값 유지)\n"
    "        _fixed_base = {\n"
    "            'MACD_Cross':        1.2,\n"
    "            'RSI_Divergence':    1.7,\n"
    "            'Bollinger_Squeeze': 1.6,\n"
    "            'ATR_Channel':       1.5,\n"
    "            'OrderBlock_SMC':    0.0,\n"
    "            'Supertrend':        0.8,\n"
    "        }\n"
    "        self.BASE_WEIGHTS = {\n"
    "            k: round(_fixed_base[k] * _cfg_boosts.get(k, 1.0), 3)\n"
    "            for k in _fixed_base\n"
    "        }"
)

if OLD2 in src2:
    src2 = src2.replace(OLD2, NEW2, 1)
    p2.write_text(src2, encoding="utf-8")
    results["FX9-2"] = "OK   BASE_WEIGHTS 초기화 제곱 버그 수정"
else:
    results["FX9-2"] = "SKIP 패턴없음"

# ════════════════════════════════════════════════════════════════
# FX9-3a — core/engine_sell.py
#   익절 쿨다운을 _sl_cooldown 에서 _profit_cooldown 으로 분리
# ════════════════════════════════════════════════════════════════
p3 = TARGETS["core/engine_sell.py"]
src3 = p3.read_text(encoding="utf-8")

OLD3 = (
    "        # [FX6-5] 익절 후 30분 재진입 쿨다운\n"
    "        if profit_rate > 0 and not _is_sl:\n"
    "            if not hasattr(self, '_sl_cooldown'):\n"
    "                self._sl_cooldown = {}\n"
    "            self._sl_cooldown[market] = _dt.datetime.now() + _dt.timedelta(minutes=30)\n"
    "            logger.info('[SELL] 익절쿨다운 %s 30min', market)"
)

NEW3 = (
    "        # [FX9-3] 익절 쿨다운 — _profit_cooldown 별도 dict로 분리 (_sl_cooldown 충돌 방지)\n"
    "        if profit_rate > 0 and not _is_sl:\n"
    "            if not hasattr(self, '_profit_cooldown'):\n"
    "                self._profit_cooldown = {}\n"
    "            self._profit_cooldown[market] = _dt.datetime.now() + _dt.timedelta(minutes=30)\n"
    "            logger.info('[SELL] 익절쿨다운 %s 30min (_profit_cooldown)', market)"
)

if OLD3 in src3:
    src3 = src3.replace(OLD3, NEW3, 1)
    p3.write_text(src3, encoding="utf-8")
    results["FX9-3a"] = "OK   engine_sell 익절쿨다운 _profit_cooldown 분리"
else:
    results["FX9-3a"] = "SKIP 패턴없음"

# ════════════════════════════════════════════════════════════════
# FX9-3b — core/engine_buy.py
#   _sl_cooldown 체크 직후에 _profit_cooldown 체크 삽입
#   삽입 위치: BEAR_REVERSAL _sl_cooldown 체크 블록 끝 바로 다음
# ════════════════════════════════════════════════════════════════
p4 = TARGETS["core/engine_buy.py"]
src4 = p4.read_text(encoding="utf-8")

# 삽입 앵커: _sl_cooldown 체크 후 _fg_idx 체크 바로 앞
OLD4 = (
    "                    _fg_idx = getattr(self.fear_greed, \"index\", 50)\n"
    "                    if _fg_idx > 25:  # [FIX] 21→25 완화"
)

NEW4 = (
    "                    # [FX9-3b] 익절 쿨다운 체크 (_profit_cooldown)\n"
    "                    import datetime as _dt_pc\n"
    "                    if hasattr(self, '_profit_cooldown') and market in self._profit_cooldown:\n"
    "                        if _dt_pc.datetime.now() < self._profit_cooldown[market]:\n"
    "                            _rem_pc = int(\n"
    "                                (self._profit_cooldown[market] - _dt_pc.datetime.now())\n"
    "                                .total_seconds() // 60\n"
    "                            )\n"
    "                            logger.info(\n"
    "                                f'[익절쿨다운] {market} 재진입 차단: {_rem_pc}분 후 가능'\n"
    "                            )\n"
    "                            return\n"
    "                        else:\n"
    "                            del self._profit_cooldown[market]\n"
    "                    _fg_idx = getattr(self.fear_greed, \"index\", 50)\n"
    "                    if _fg_idx > 25:  # [FIX] 21→25 완화"
)

if OLD4 in src4:
    src4 = src4.replace(OLD4, NEW4, 1)
    p4.write_text(src4, encoding="utf-8")
    results["FX9-3b"] = "OK   engine_buy _profit_cooldown 체크 삽입"
else:
    results["FX9-3b"] = "SKIP 패턴없음"

# ════════════════════════════════════════════════════════════════
# 컴파일 검증
# ════════════════════════════════════════════════════════════════
compile_targets = [
    ("risk/position_sizer.py",           p1),
    ("strategies/v2/ensemble_engine.py", p2),
    ("core/engine_sell.py",              p3),
    ("core/engine_buy.py",               p4),
]

all_ok = True
for label, path in compile_targets:
    try:
        py_compile.compile(str(path), doraise=True)
        print(f"컴파일 OK  {label}")
    except py_compile.PyCompileError as e:
        print(f"컴파일 FAIL: {e}")
        # 자동 복원
        backup = ARCHIVE / pathlib.Path(label)
        if backup.exists():
            shutil.copy2(backup, path)
            print(f"자동 복원: {backup}")
        all_ok = False

# ════════════════════════════════════════════════════════════════
# 결과 출력
# ════════════════════════════════════════════════════════════════
print()
for k, v in results.items():
    print(f"{k}  {v}")
print(f"\n백업: {ARCHIVE}")

if all_ok:
    print("\n✅ FX9 전체 패치 성공")
else:
    print("\n❌ 컴파일 실패 — 자동 복원 완료, 출력 확인 필요")
