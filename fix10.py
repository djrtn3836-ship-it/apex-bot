# fix10.py — FX10-1~3 패치
# FX10-1: ensemble_engine.py decide() KeyError 수정 — _weights 안전 접근
# FX10-2: engine_buy.py _run_strategies() OrderBlock_SMC 완전 차단
# FX10-3: engine_sell.py 익절쿨다운 로그 포맷 %s → f-string 수정

import pathlib, shutil, py_compile, datetime

ROOT    = pathlib.Path(".")
ARCHIVE = ROOT / "archive" / f"fx10_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
ARCHIVE.mkdir(parents=True, exist_ok=True)

TARGETS = {
    "strategies/v2/ensemble_engine.py": ROOT / "strategies/v2/ensemble_engine.py",
    "core/engine_buy.py":               ROOT / "core/engine_buy.py",
    "core/engine_sell.py":              ROOT / "core/engine_sell.py",
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
# FX10-1 — strategies/v2/ensemble_engine.py
#   decide() 내부 self._weights[name] → KeyError 방지
#   signals 루프에서 _weights에 없는 전략은 스킵
# ════════════════════════════════════════════════════════════════
p1 = TARGETS["strategies/v2/ensemble_engine.py"]
src1 = p1.read_text(encoding="utf-8")

OLD1 = (
    "            for name, sig in signals.items():\n"
    "                w     = self._weights[name].dynamic_weight\n"
    "                boost = regime_boosts.get(name, 1.0)  # [U1-PATCH] 0.0→1.0 기본배율\n"
    "                final_w = w * boost  # [U1-PATCH] 덧셈→곱셈: 레짐부스트를 배율로 적용\n"
    "                score   = (sig.score * 0.4 + sig.confidence * 0.6) * final_w\n"
    "                total_score  += score\n"
    "                total_weight += final_w\n"
    "                if score > best_score:\n"
    "                    best_score = score\n"
    "                    best_name  = name"
)

NEW1 = (
    "            for name, sig in signals.items():\n"
    "                # [FX10-1] _weights에 없는 전략(OrderBlock 등) KeyError 방지\n"
    "                if name not in self._weights:\n"
    "                    logger.debug(f'[Ensemble] {name} _weights 미등록 → 스킵')\n"
    "                    continue\n"
    "                w     = self._weights[name].dynamic_weight\n"
    "                boost = regime_boosts.get(name, 1.0)  # [U1-PATCH] 0.0→1.0 기본배율\n"
    "                final_w = w * boost  # [U1-PATCH] 덧셈→곱셈: 레짐부스트를 배율로 적용\n"
    "                score   = (sig.score * 0.4 + sig.confidence * 0.6) * final_w\n"
    "                total_score  += score\n"
    "                total_weight += final_w\n"
    "                if score > best_score:\n"
    "                    best_score = score\n"
    "                    best_name  = name"
)

if OLD1 in src1:
    src1 = src1.replace(OLD1, NEW1, 1)
    p1.write_text(src1, encoding="utf-8")
    results["FX10-1"] = "OK   decide() _weights KeyError 방지"
else:
    results["FX10-1"] = "SKIP 패턴없음"

# ════════════════════════════════════════════════════════════════
# FX10-2 — core/engine_buy.py
#   _run_strategies() 결과에서 OrderBlock_SMC 신호 필터링
#   signals 수집 후 즉시 OrderBlock_SMC 제거
# ════════════════════════════════════════════════════════════════
p2 = TARGETS["core/engine_buy.py"]
src2 = p2.read_text(encoding="utf-8")

# _run_strategies 반환 직후 필터링 삽입
OLD2 = (
    "            signals  = await self._run_strategies(market, df_processed)\n"
    "            # [VP2-PATCH] 전략 신호 상세 디버그\n"
    "            if signals:"
)

NEW2 = (
    "            signals  = await self._run_strategies(market, df_processed)\n"
    "            # [FX10-2] OrderBlock_SMC 완전 차단 — weight=0.0 전략 신호 제거\n"
    "            _DISABLED_STRATS = {'OrderBlock_SMC', 'VolBreakout', 'VWAP_Reversion'}\n"
    "            signals = [\n"
    "                _s for _s in (signals or [])\n"
    "                if getattr(_s, 'strategy_name', '') not in _DISABLED_STRATS\n"
    "            ]\n"
    "            # [VP2-PATCH] 전략 신호 상세 디버그\n"
    "            if signals:"
)

if OLD2 in src2:
    src2 = src2.replace(OLD2, NEW2, 1)
    p2.write_text(src2, encoding="utf-8")
    results["FX10-2"] = "OK   _run_strategies OrderBlock_SMC 필터링 삽입"
else:
    results["FX10-2"] = "SKIP 패턴없음"

# ════════════════════════════════════════════════════════════════
# FX10-3 — core/engine_sell.py
#   익절쿨다운 로그 포맷 %s → f-string 수정
# ════════════════════════════════════════════════════════════════
p3 = TARGETS["core/engine_sell.py"]
src3 = p3.read_text(encoding="utf-8")

OLD3 = "            logger.info('[SELL] 익절쿨다운 %s 30min (_profit_cooldown)', market)"
NEW3 = "            logger.info(f'[SELL] 익절쿨다운 {market} 30min (_profit_cooldown)')"

if OLD3 in src3:
    src3 = src3.replace(OLD3, NEW3, 1)
    p3.write_text(src3, encoding="utf-8")
    results["FX10-3"] = "OK   익절쿨다운 로그 f-string 수정"
else:
    results["FX10-3"] = "SKIP 패턴없음"

# ════════════════════════════════════════════════════════════════
# 컴파일 검증
# ════════════════════════════════════════════════════════════════
compile_targets = [
    ("strategies/v2/ensemble_engine.py", p1),
    ("core/engine_buy.py",               p2),
    ("core/engine_sell.py",              p3),
]

all_ok = True
for label, path in compile_targets:
    try:
        py_compile.compile(str(path), doraise=True)
        print(f"컴파일 OK  {label}")
    except py_compile.PyCompileError as e:
        print(f"컴파일 FAIL: {e}")
        backup = ARCHIVE / pathlib.Path(label)
        if backup.exists():
            shutil.copy2(backup, path)
            print(f"자동 복원: {backup}")
        all_ok = False

print()
for k, v in results.items():
    print(f"{k}  {v}")
print(f"\n백업: {ARCHIVE}")

if all_ok:
    print("\n✅ FX10 전체 패치 성공")
else:
    print("\n❌ 컴파일 실패 — 자동 복원 완료")
