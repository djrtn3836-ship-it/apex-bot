# final_patch_v1.py
# APEX BOT 2026-05-05 정밀 분석 기반 완전 패치
# 패치 목록:
#   FP1 - predictor.py:  U3 signal_idx 조건 수정 (idx==1 → 조건 제거)
#   FP2 - predictor.py:  predict_batch()에 U3 동일 로직 추가
#   FP3 - ensemble_engine.py: config boost 절대값 덮어쓰기 → 배율 적용으로 수정
#   FP4 - ensemble_engine.py: EnsembleDecision.confidence = avg(signal_confidence)
#   FP5 - position_sizer.py:  confidence=0 시 0 반환 게이트 추가
#   FP6 - engine_cycle.py:    _cycle() 직접 호출 시 _cb_main_loop_active 초기화
#   FP7 - signal_combiner.py: _filter_by_regime DISABLED 사전 필터링
#   FP8 - engine_buy.py:      BEAR_REVERSAL 강제 BUY에 bear_reversal=True 명시
# ────────────────────────────────────────────────────────────────────

import pathlib, shutil, datetime, py_compile, sys

ROOT = pathlib.Path(__file__).parent
ARCHIVE = ROOT / f"archive/final_patch_{datetime.datetime.now():%Y%m%d_%H%M%S}"
ARCHIVE.mkdir(parents=True, exist_ok=True)

results = []

def backup(path: pathlib.Path):
    dest = ARCHIVE / path.relative_to(ROOT)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)

def apply(tag, path_rel, old_lines, new_lines):
    path = ROOT / path_rel
    if not path.exists():
        results.append((tag, "SKIP", "파일 없음"))
        return
    backup(path)
    text = path.read_text(encoding="utf-8")
    old_block = "\n".join(old_lines)
    new_block  = "\n".join(new_lines)
    if f"[{tag}]" in text:
        results.append((tag, "SKIP", "이미 적용됨"))
        return
    if old_block not in text:
        results.append((tag, "SKIP", "패턴 없음"))
        return
    new_text = text.replace(old_block, new_block, 1)
    path.write_text(new_text, encoding="utf-8")
    try:
        py_compile.compile(str(path), doraise=True)
        results.append((tag, "OK", ""))
    except py_compile.PyCompileError as e:
        path.write_text(text, encoding="utf-8")
        results.append((tag, "ROLLBACK", str(e)))

# ════════════════════════════════════════════════════
# FP1: predictor.py — U3 signal_idx 조건 수정
# ════════════════════════════════════════════════════
apply("FP1", "models/inference/predictor.py",
    old_lines=[
        "            if confidence < self.MIN_CONFIDENCE:",
        "                # buy_prob 별도 구제: softmax max가 낮아도 BUY 확률이 높으면 BUY",
        "                if float(proba_np[0]) >= 0.33 and signal_idx == 1:",
        "                    signal = \"BUY\"",
        "                    confidence = float(proba_np[0])",
        "                else:",
        "                    signal = \"HOLD\"",
    ],
    new_lines=[
        "            if confidence < self.MIN_CONFIDENCE:",
        "                # [FP1-PATCH] signal_idx 조건 제거: idx==1(HOLD)만 구제하던 오류 수정",
        "                # BUY 확률이 0.33 이상이면 argmax 무관하게 BUY 구제",
        "                if float(proba_np[0]) >= 0.33:",
        "                    signal = \"BUY\"",
        "                    confidence = float(proba_np[0])",
        "                else:",
        "                    signal = \"HOLD\"",
    ],
)

# ════════════════════════════════════════════════════
# FP2: predictor.py — predict_batch()에 U3 동일 로직 추가
# ════════════════════════════════════════════════════
apply("FP2", "models/inference/predictor.py",
    old_lines=[
        "                signal = self.CLASS_NAMES[idx]",
        "                if confidence < self.MIN_CONFIDENCE:",
        "                    signal = \"HOLD\"",
    ],
    new_lines=[
        "                signal = self.CLASS_NAMES[idx]",
        "                if confidence < self.MIN_CONFIDENCE:",
        "                    # [FP2-PATCH] predict_batch U3 동기화: buy_prob >= 0.33 구제",
        "                    if float(p[0]) >= 0.33:",
        "                        signal = \"BUY\"",
        "                        confidence = float(p[0])",
        "                    else:",
        "                        signal = \"HOLD\"",
    ],
)

# ════════════════════════════════════════════════════
# FP3: ensemble_engine.py — config boost 배율 적용
# ════════════════════════════════════════════════════
apply("FP3", "strategies/v2/ensemble_engine.py",
    old_lines=[
        "        _cfg_boosts = self._load_base_weights()",
        "        self.BASE_WEIGHTS = {**self.BASE_WEIGHTS, **_cfg_boosts}",
    ],
    new_lines=[
        "        _cfg_boosts = self._load_base_weights()",
        "        # [FP3-PATCH] config boost를 절대값이 아닌 배율로 적용",
        "        # 예: MACD_Cross base=1.2, boost=1.3 → 1.2*1.3=1.56",
        "        self.BASE_WEIGHTS = {",
        "            k: round(self.BASE_WEIGHTS.get(k, v) * v, 3)",
        "            for k, v in {**self.BASE_WEIGHTS, **{",
        "                k2: _cfg_boosts.get(k2, 1.0)",
        "                for k2 in self.BASE_WEIGHTS",
        "            }}.items()",
        "        }",
    ],
)

# ════════════════════════════════════════════════════
# FP4: ensemble_engine.py — EnsembleDecision.confidence 수정
# ════════════════════════════════════════════════════
apply("FP4", "strategies/v2/ensemble_engine.py",
    old_lines=[
        "            return EnsembleDecision(",
        "                should_enter=should_enter,",
        "                final_score=normalized,",
        "                confidence=normalized,",
    ],
    new_lines=[
        "            # [FP4-PATCH] confidence = 실제 신호 평균 confidence (≠ normalized score)",
        "            _avg_conf = (",
        "                sum(sig.confidence for sig in signals.values()) / len(signals)",
        "                if signals else normalized",
        "            )",
        "            return EnsembleDecision(",
        "                should_enter=should_enter,",
        "                final_score=normalized,",
        "                confidence=_avg_conf,",
    ],
)

# ════════════════════════════════════════════════════
# FP5: position_sizer.py — confidence=0 시 0 반환 게이트
# ════════════════════════════════════════════════════
apply("FP5", "risk/position_sizer.py",
    old_lines=[
        "        if total_capital <= 0:",
        "            return 0.0",
        "",
        "        # ── Step 1: Kelly fraction ───────────────────────────────",
    ],
    new_lines=[
        "        if total_capital <= 0:",
        "            return 0.0",
        "",
        "        # [FP5-PATCH] confidence=0 (ML 실패/HOLD) 시 매수 차단",
        "        if confidence <= 0.0:",
        "            logger.debug(f'[Kelly-CONF0] {strategy} {market} | confidence=0 → 매수 차단')",
        "            return 0.0",
        "",
        "        # ── Step 1: Kelly fraction ───────────────────────────────",
    ],
)

# ════════════════════════════════════════════════════
# FP6: engine_cycle.py — _cycle() CB 초기화 보강
# ════════════════════════════════════════════════════
apply("FP6", "core/engine_cycle.py",
    old_lines=[
        "        # [MDD-L3] 포트폴리오 서킷브레이커",
    ],
    new_lines=[
        "        # [FP6-PATCH] _cycle() 직접 호출 시 _cb_main_loop_active 초기화 보장",
        "        if not hasattr(self, '_cb_main_loop_active'):",
        "            self._cb_main_loop_active = False",
        "        # [MDD-L3] 포트폴리오 서킷브레이커",
    ],
)

# ════════════════════════════════════════════════════
# FP7: signal_combiner.py — _filter_by_regime DISABLED 사전 필터
# ════════════════════════════════════════════════════
apply("FP7", "signals/signal_combiner.py",
    old_lines=[
        "    def _filter_by_regime(self, signals: List[Signal], regime: str) -> List[Signal]:",
        "        preferred = self.REGIME_PREFERRED.get(regime.upper(), None)",
        "        if preferred is None:",
        "            return signals",
    ],
    new_lines=[
        "    def _filter_by_regime(self, signals: List[Signal], regime: str) -> List[Signal]:",
        "        # [FP7-PATCH] 비활성 전략 사전 필터링 (부스트 연산 불필요 방지)",
        "        signals = [s for s in signals if s.strategy_name not in DISABLED_STRATEGIES",
        "                   and self.STRATEGY_WEIGHTS.get(s.strategy_name, 1.0) > 0.0]",
        "        preferred = self.REGIME_PREFERRED.get(regime.upper(), None)",
        "        if preferred is None:",
        "            return signals",
    ],
)

# ════════════════════════════════════════════════════
# FP8: engine_buy.py — BEAR_REVERSAL 강제 BUY bear_reversal=True 명시
# ════════════════════════════════════════════════════
apply("FP8", "core/engine_buy.py",
    old_lines=[
        "                    combined = CombinedSignal(",
        "                        market=market,",
        "                        signal_type=SignalType.BUY,",
        "                        score=0.63,",
        "                        confidence=0.63,",
        "                        agreement_rate=1.0,",
        "                        contributing_strategies=[\"BEAR_REVERSAL\"],",
        "                        reasons=[\"극단적 공포 역발상 매수\"],",
        "                    )",
    ],
    new_lines=[
        "                    combined = CombinedSignal(",
        "                        market=market,",
        "                        signal_type=SignalType.BUY,",
        "                        score=0.63,",
        "                        confidence=0.63,",
        "                        agreement_rate=1.0,",
        "                        contributing_strategies=[\"BEAR_REVERSAL\"],",
        "                        reasons=[\"극단적 공포 역발상 매수\"],",
        "                        bear_reversal=True,  # [FP8-PATCH] 명시적 플래그",
        "                    )",
    ],
)

# ════════════════════════════════════════════════════
# 결과 출력
# ════════════════════════════════════════════════════
print("=" * 68)
print("  APEX BOT — final_patch_v1.py")
print("=" * 68)
ok = skip = fail = 0
for tag, status, msg in results:
    icon = "✅" if status == "OK" else ("⏭ " if status == "SKIP" else "❌")
    note = f" ({msg})" if msg else ""
    print(f"  {icon} {status:<10} | {tag}{note}")
    if status == "OK":    ok += 1
    elif status == "SKIP": skip += 1
    else: fail += 1
print(f"\n  OK={ok}  SKIP={skip}  FAIL/ROLLBACK={fail}")
print(f"  백업: {ARCHIVE}")
print("=" * 68)
if fail == 0:
    print("\n✅ 완료! 다음 단계:")
    print("  git add -A")
    print("  git commit -m 'fix: final_patch_v1 — FP1~FP8 신규 버그 수정'")
    print("  git push origin main")
    print("  python main.py --mode paper")
else:
    print(f"\n❌ ROLLBACK {fail}건 발생. 로그를 확인하세요.")
    sys.exit(1)
