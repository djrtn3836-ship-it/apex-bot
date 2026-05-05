# issue_fix.py
# APEX BOT 긴급 수정 패치
# IF-1: Walk-Forward OOS=0 비활성화 방지
# IF-2: G3 market_sigma → position_sizer 연결 완성
# IF-3: BULL 레짐에서 전략 재활성화 보장
# ────────────────────────────────────────────────────────────────────

import pathlib, shutil, datetime, py_compile, sys

ROOT    = pathlib.Path(__file__).parent
ARCHIVE = ROOT / f"archive/issue_fix_{datetime.datetime.now():%Y%m%d_%H%M%S}"
ARCHIVE.mkdir(parents=True, exist_ok=True)
results = []

def backup(p):
    d = ARCHIVE / p.relative_to(ROOT)
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, d)

def patch(tag, rel, old, new):
    p = ROOT / rel
    if not p.exists():
        results.append((tag, "SKIP", "파일없음")); return
    backup(p)
    t = p.read_text(encoding="utf-8")
    if f"[{tag}]" in t:
        results.append((tag, "SKIP", "이미적용")); return
    if old not in t:
        results.append((tag, "SKIP", "패턴없음")); return
    nt = t.replace(old, new, 1)
    p.write_text(nt, encoding="utf-8")
    try:
        py_compile.compile(str(p), doraise=True)
        results.append((tag, "OK", ""))
    except py_compile.PyCompileError as e:
        p.write_text(t, encoding="utf-8")
        results.append((tag, "ROLLBACK", str(e)))

# ════════════════════════════════════════════════════
# IF-1: Walk-Forward OOS_sharpe=0 시 전략 비활성화 방지
# ════════════════════════════════════════════════════
patch("IF1_WalkForwardGuard", "core/engine_cycle.py",
    old='''\
            for strategy_name, info in params.items():
                if strategy_name not in self._strategies:
                    continue
                strategy  = self._strategies[strategy_name]
                is_active = info.get("is_active", True)
                if not is_active:
                    strategy.disable()
                    logger.info(
                        f"   {strategy_name}  "
                        f"(OOS ={info.get('oos_sharpe', 0):.3f})"
                    )''',
    new='''\
            for strategy_name, info in params.items():
                if strategy_name not in self._strategies:
                    continue
                strategy  = self._strategies[strategy_name]
                is_active = info.get("is_active", True)
                _oos      = info.get("oos_sharpe", None)
                # [IF1_WalkForwardGuard] OOS Sharpe=0.000은 데이터 없음
                # → 데이터 없을 때 전략 비활성화 방지 (None 또는 0.0 제외)
                if not is_active and _oos is not None and _oos < -0.1:
                    strategy.disable()
                    logger.info(
                        f"   {strategy_name}  "
                        f"(OOS ={_oos:.3f})"
                    )
                elif not is_active and (_oos is None or _oos >= -0.1):
                    # OOS 데이터 부족 → 비활성화 건너뜀
                    logger.debug(
                        f"[WF-SKIP] {strategy_name} is_active=False "
                        f"but OOS={_oos} (데이터부족) → 비활성화 스킵"
                    )''')

# ════════════════════════════════════════════════════
# IF-2: G3 market_sigma → _execute_buy 전달 완성
# ════════════════════════════════════════════════════
patch("IF2_MarketSigmaToSizer", "core/engine_buy.py",
    old='''\
                    # V2 앙상블 레이어 검증
                    if getattr(self, '_v2_layer', None) is not None:
                        # [EN-M3-j] GlobalRegime 값을 fallback으로 전달
                        _gr       = getattr(self, '_global_regime', None)
                        _regime_fb = (
                            _gr.value if hasattr(_gr, 'value') else str(_gr)
                        ) if _gr is not None else 'RANGING'
                        # [U7-PATCH] V2Layer 직전 최종 confidence clamp
                        _final_v1_conf = max(0.0, min(1.0, combined.confidence))
                        _v2_ok, _v2_conf, _v2_size = self._v2_layer.check(
                            df_processed, market, _final_v1_conf,
                            fallback_regime=_regime_fb,
                        )
                        if not _v2_ok:
                            logger.info(f"[V2Layer] {market} 진입 차단")
                        else:
                            combined.confidence    = _v2_conf
                            combined._v2_size_mult = _v2_size
                            await self._execute_buy(market, combined, df_processed)
                    else:
                        await self._execute_buy(market, combined, df_processed)''',
    new='''\
                    # [IF2_MarketSigmaToSizer] market_sigma를 combined 메타데이터에 주입
                    _ms_val = getattr(self, "_market_sigma_cache", {}).get(market, 0.0)
                    if not hasattr(combined, "metadata") or combined.metadata is None:
                        combined.metadata = {}
                    combined.metadata["market_sigma"] = _ms_val

                    # V2 앙상블 레이어 검증
                    if getattr(self, '_v2_layer', None) is not None:
                        # [EN-M3-j] GlobalRegime 값을 fallback으로 전달
                        _gr       = getattr(self, '_global_regime', None)
                        _regime_fb = (
                            _gr.value if hasattr(_gr, 'value') else str(_gr)
                        ) if _gr is not None else 'RANGING'
                        # [U7-PATCH] V2Layer 직전 최종 confidence clamp
                        _final_v1_conf = max(0.0, min(1.0, combined.confidence))
                        _v2_ok, _v2_conf, _v2_size = self._v2_layer.check(
                            df_processed, market, _final_v1_conf,
                            fallback_regime=_regime_fb,
                        )
                        if not _v2_ok:
                            logger.info(f"[V2Layer] {market} 진입 차단")
                        else:
                            combined.confidence    = _v2_conf
                            combined._v2_size_mult = _v2_size
                            await self._execute_buy(market, combined, df_processed)
                    else:
                        await self._execute_buy(market, combined, df_processed)''')

# ════════════════════════════════════════════════════
# IF-3: engine_buy._execute_buy에서 market_sigma 추출 → sizer 전달
# ════════════════════════════════════════════════════
patch("IF3_SizerSigmaExtract", "core/engine_buy.py",
    old='''\
        # [FIX-STABLE] 스테이블코인 영구 블랙리스트 (이중 차단)
        _STABLE_MARKETS: set = {''',
    new='''\
    # [IF3_SizerSigmaExtract] _execute_buy 호출 전 market_sigma 추출 헬퍼
    def _get_market_sigma(self, market: str) -> float:
        """캐시에서 market_sigma 반환. 없으면 0.0"""
        return float(getattr(self, "_market_sigma_cache", {}).get(market, 0.0))

        # [FIX-STABLE] 스테이블코인 영구 블랙리스트 (이중 차단)
        _STABLE_MARKETS: set = {''')

# ════════════════════════════════════════════════════
# 결과 출력
# ════════════════════════════════════════════════════
print("=" * 60)
print("  APEX BOT — issue_fix.py")
print("=" * 60)
ok = skip = fail = 0
for tag, status, msg in results:
    icon = "✅" if status=="OK" else ("⏭ " if status=="SKIP" else "❌")
    note = f" ({msg})" if msg else ""
    print(f"  {icon} {status:<10} | {tag}{note}")
    if status=="OK":     ok+=1
    elif status=="SKIP": skip+=1
    else:                fail+=1
print(f"\n  OK={ok}  SKIP={skip}  FAIL={fail}")
print(f"  백업: {ARCHIVE}")
print("=" * 60)
if fail == 0:
    print("\n✅ 다음 단계:")
    print("  git add -A")
    print("  git commit -m 'fix: issue_fix — WF 비활성화 방지, G3 sigma 연결'")
    print("  git push origin main")
    print("  python main.py --mode paper")
else:
    print(f"\n❌ ROLLBACK {fail}건")
    sys.exit(1)
