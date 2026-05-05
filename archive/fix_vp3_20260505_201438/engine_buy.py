"""
core/engine_buy.py
─────────────────────────────────────────────────────────────
매수 분석 및 실행 관련 Mixin

포함 메서드:
    _analyze_market           : 시장 분석 및 매수 신호 판단
    _get_preferred_strategies : 시장별 선호 전략 반환
    _run_strategies           : 전략 실행 및 신호 수집
    _evaluate_entry_signals   : 진입 신호 평가
    _execute_buy              : 매수 실행
─────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import time

from datetime import datetime
from execution.executor import OrderExecutor, ExecutionRequest, OrderSide
from utils.logger import setup_logger, log_trade, log_signal, log_risk
from signals.filters.regime_detector import RegimeDetector, MarketRegime
from core.market_regime import GlobalMarketRegimeDetector, GlobalRegime
import asyncio
from typing import Optional
from loguru import logger
from core.engine_utils import _floor_vol, calc_position_size, calc_exit_plan
from core.surge_detector import SurgeDetector, SurgeResult


class EngineBuyMixin:
    """매수 분석, 신호 평가, 매수 실행 관련 메서드 Mixin"""

    # [FIX-STABLE] 스테이블코인 영구 블랙리스트 (이중 차단)
    _STABLE_MARKETS: set = {
        "KRW-USDT", "KRW-USDC", "KRW-USD1", "KRW-BUSD", "KRW-DAI",
        "KRW-TUSD", "KRW-USDP", "KRW-FDUSD", "KRW-PYUSD", "KRW-USDS",
    }


    # ── PATCH-1: 종목별 분석 Lock (이중매수 TOCTOU 방지) ────────────────
    def _get_analyze_lock(self, market: str):
        """시장 분석용 종목별 asyncio.Lock 반환."""
        import asyncio as _aio
        if not hasattr(self, "_analyze_locks"):
            self._analyze_locks = {}
        if market not in self._analyze_locks:
            self._analyze_locks[market] = _aio.Lock()
        return self._analyze_locks[market]
    # ────────────────────────────────────────────────────────────────────
    async def _analyze_market(self, market: str):
        # PATCH-1: 종목별 Lock으로 동시 진입 차단
        _lock = self._get_analyze_lock(market)
        if _lock.locked():
            # 이미 분석 중 → 즉시 스킵 (이중매수 방지)
            return
        async with _lock:
            await self._analyze_market_inner(market)

    async def _analyze_market_inner(self, market: str):
        # [E-H1] pending_surge_queue TTL 600초 체크
        import time as _time_mod
        _now_ts = _time_mod.time()
        _fresh_queue = []
        while self._pending_surge_queue:
            _item = self._pending_surge_queue.popleft()
            _market_q, _score_q, _detected_at = _item
            if _now_ts - _detected_at <= 600:
                _fresh_queue.append(_item)
            else:
                logger.debug(
                    f"[PENDING-TTL] {_market_q} 서지 신호 만료 "
                    f"(경과 {_now_ts - _detected_at:.0f}초 > 600초) → 폐기"
                )
        for _item in _fresh_queue:
            self._pending_surge_queue.append(_item)
        # TTL 체크 완료 ─────────────────────────────────────

        # [MDD-L3] 서킷브레이커 활성 시 매수 전면 차단
        if getattr(self, "_circuit_breaker_active", False):
            logger.debug(f"[MDD-L3] {market} 매수 차단 (서킷브레이커 활성)")
            return
        # Dynamic ML threshold based on Fear & Greed Index (v2.0.4 fixed)
        fgi_idx = getattr(self.fear_greed, 'index', None) or 50
        logger.info("[ANALYZE] %s 진입" % market)
        # [FIX-STABLE] 스테이블코인 이중 차단
        if market in self._STABLE_MARKETS:
            logger.debug(f"[STABLE-BLOCK] {market} 스테이블코인 매수 분석 차단")
            return None
        _base_buy  = self.settings.risk.buy_signal_threshold   # 0.35
        _base_sell = self.settings.risk.sell_signal_threshold  # 0.35
        if fgi_idx < 20:    # Extreme Fear -> lower threshold (easier to buy)
            buy_threshold  = _base_buy  # [FIX] FGI 하향 제거
            sell_threshold = max(0.20, _base_sell - 0.10)
        elif fgi_idx < 40:  # Fear
            buy_threshold  = _base_buy  # [FIX] FGI Fear 하향 제거
            sell_threshold = _base_sell
        elif fgi_idx > 80:  # Extreme Greed -> raise threshold (harder to buy)
            buy_threshold  = _base_buy  + 0.15
            sell_threshold = _base_sell + 0.10
        else:               # Neutral / Greed
            buy_threshold  = _base_buy
            sell_threshold = _base_sell
        from signals.signal_combiner import CombinedSignal, SignalType

        if self.portfolio.position_count >= self.settings.trading.max_positions:
            logger.debug(f"[ANALYZE] {market} 최대포지션 차단 ({self.portfolio.position_count}/{self.settings.trading.max_positions})")  # PATCH-3
            return
        if self.portfolio.is_position_open(market):
            logger.debug(f"[ANALYZE] {market} 이미포지션 보유 차단")  # PATCH-3
            return

        last_signal = self._last_signal_time.get(market, 0)
        _cooldown   = (
            60 if market in getattr(self, "_bear_reversal_markets", set())
            else 240  # [FIX] 쿨다운 240초 고정
        )
        if time.time() - last_signal < _cooldown:
            logger.warning(f"[ANALYZE] {market} cooldown 차단 (남은={_cooldown - (time.time()-last_signal):.0f}s)")
            return

        try:
            open_pos         = list(self.portfolio.open_positions.keys())
            can_buy_corr, corr_reason = self.correlation_filter.can_buy(market, open_pos)
            if not can_buy_corr:
                logger.info(f"  ({market}): {corr_reason}")
                return

            logger.info(f"[ANALYZE] {market} corr통과 → kimchi 체크")
            can_buy_kimchi, kimchi_reason, premium = self.kimchi_monitor.can_buy(market)
            if not can_buy_kimchi:
                logger.info(
                    f"   ({market}): {kimchi_reason} "
                    f"[프리미엄 {premium:.1f}%]"
                )
                return

            logger.info(f"[ANALYZE] {market} kimchi통과 → df_1h 로드")
            # NpyCache 우선 조회 → API fallback (Rate Limit 대응 v3.1)
            df_1h = None
            try:
                df_1h = self.cache_manager.get_ohlcv(market, "1h")
                if df_1h is not None and len(df_1h) >= 50:
                    logger.info(f"[ANALYZE] {market} 캐시 로드 OK ({len(df_1h)}행)")
            except Exception as _ce:
                logger.info(f"[ANALYZE] {market} 캐시 조회 오류: {_ce}")
                df_1h = None
            if df_1h is None or len(df_1h) < 50:
                try:
                    df_1h = await self.rest_collector.get_ohlcv(market, "minute60", 200)
                except Exception as _ae:
                    logger.info(f"[ANALYZE] {market} API 조회 오류: {_ae}")
                    df_1h = None
            if df_1h is None or len(df_1h) < 50:
                logger.info(f"[ANALYZE] {market} df_1h 없음 (캐시+API 모두 실패)")
                return

            # ══════════════════════════════════════════════════════
            # [SURGE-FASTENTRY] SURGE A급 이상 → ML/TrendFilter 생략
            # SurgeDetector score >= 0.35  # [FIX] 0.6->0.35 score scale unified5 + is_surge=True 이면
            # TrendFilter/VolumeProfile/ML 파이프라인 우회하고
            # _evaluate_entry_signals() 로 직행
            # ══════════════════════════════════════════════════════
            _surge_cache  = getattr(self, "_surge_cache", {})
            _surge_info   = _surge_cache.get(market, {})
            # [PHASE1-REGIME] GlobalRegime 기반 SURGE 임계값 자동 조정
            _gr_p1      = getattr(self, "_global_regime", None)
            _gr_val_p1  = str(getattr(_gr_p1, "value", _gr_p1 or "UNKNOWN")).upper()
            _surge_thr  = {
                "BULL":       0.40,   # 강세장: 완화
                "RECOVERY":   0.45,   # 회복장: 기본
                "BEAR_WATCH": 0.55,   # 약세경계: 강화 ← 현재 시장
                "BEAR":       9.99,   # 약세장: 사실상 차단
            }.get(_gr_val_p1, 0.45)

            # [PHASE1-TIMEBLOCK] 새벽 00:00~08:59 KST SURGE 차단
            _kst_h_p1   = datetime.now().hour  # 서버 KST 기준
            _time_ok_p1 = not (0 <= _kst_h_p1 < 9)

            _is_surge_fast = (
                _surge_info.get("is_surge", False)
                and _surge_info.get("score", 0.0) >= _surge_thr   # [PHASE1] 레짐 자동 임계값
                and not _surge_info.get("pump_dump", False)
                and _time_ok_p1                                    # [PHASE1] 시간대 필터
            )
            if not _time_ok_p1 and _surge_info.get("is_surge", False):
                logger.debug(
                    f"[SURGE-TIMEBLOCK] {market} {_kst_h_p1}시 새벽차단 "
                    f"(score={_surge_info.get('score',0):.3f})"
                )
            if _surge_info.get("is_surge", False) and _surge_info.get("score", 0) < _surge_thr and _time_ok_p1:
                logger.debug(
                    f"[SURGE-REGIME-BLOCK] {market} score={_surge_info.get('score',0):.3f} "
                    f"< regime_thr={_surge_thr} ({_gr_val_p1})"
                )
            if _is_surge_fast:
                _sg = _surge_info.get("grade", "")
                _ss = _surge_info.get("score", 0.0)
                _sr = _surge_info.get("reason", "")
                # ── [SURGE 완전 독립] ML/전략 파이프라인 완전 우회 ──
                # [PHASE2-C] SURGE 전략별 슬롯 쿼터 체크 (가장 먼저)
                # ── [PHASE2] SURGE_FASTENTRY 완전 비활성화 ──────────────────
                # 누적 손실 -41.02% (326 trades, WR 46%) → 즉시 차단
                # [PHASE2] SURGE_FASTENTRY 영구 비활성화
                # 사유: 누적 손실 -41.02% (326 trades, WR 46%)
                logger.debug(f"[SURGE-DISABLED] {market} → 스킵")
                return

            try:
                df_1d = await self.rest_collector.get_ohlcv(market, "day", 210)
                if df_1d is None or len(df_1d) < 5:
                    raise ValueError("  ")
                _strategy_hint = (
                    "BEAR_REVERSAL"
                    if market in getattr(self, "_bear_reversal_markets", set())
                    else "default"
                )
                _trend = self.trend_filter.is_buy_allowed(
                    daily_df=df_1d, strategy=_strategy_hint
                )
                if not _trend["allowed"]:
                    logger.info(
                        f"[TrendFilter]   ({market}): {_trend['reason']}"
                    )
                    return
                logger.info(
                    f"[TrendFilter] {market}: {_trend['reason']} "
                    f"(={_trend.get('regime', '?')})"
                )
            except Exception as _te:
                logger.info(f"[TrendFilter]  ({market}): {_te}")

            try:
                _vp = self.volume_profile.analyze(df_1h)
                if _vp is not None:
                    _cur_price = float(df_1h["close"].iloc[-1])
                    _vp_sr     = self.volume_profile.get_nearest_support_resistance(
                        df_1h, _cur_price
                    )
                    _rr  = _vp_sr.get("risk_reward", 1.0)
                    _sup = _vp_sr.get("support",     0)
                    _res = _vp_sr.get("resistance",  0)
                    # [FIX-SURGE] SURGE 30pt+ 이면 RR 음수 차단 우회
                    _surge_score = 0.0
                    if hasattr(self, '_market_change_rates'):
                        _surge_score = self._market_change_rates.get(market, 0.0) * 100
                    # [VP1-PATCH] GlobalRegime 기반 RR 임계값 동적 조정
                    _gr_vp = str(getattr(getattr(self, "_global_regime", None), "value",
                                 getattr(self, "_global_regime", "UNKNOWN") or "UNKNOWN")).upper()
                    _rr_thr = {
                        "BULL":       -0.60,  # BULL: 완화 (단기 저항 근접 허용)
                        "RECOVERY":   -0.45,  # 회복: 중간
                        "BEAR_WATCH": -0.30,  # 약세경계: 기존값 유지
                        "BEAR":       -0.20,  # 약세: 엄격
                    }.get(_gr_vp, -0.30)
                    if _rr < _rr_thr and _sup > 0 and _res > 0 and _surge_score < 30.0:
                        logger.info(
                            f'[VolumeProfile] ({market}): '
                            f'RR={_rr:.2f} 저항={_res:,.0f} 지지={_sup:,.0f} → 차단'
                        )
                        return
                    elif _rr < -0.3 and _surge_score >= 30.0:
                        logger.info(
                            f'[VolumeProfile-SURGE] ({market}): '
                            f'RR={_rr:.2f} SURGE={_surge_score:.1f}pt → RR차단 우회'
                        )
                    logger.info(
                        f"[VolumeProfile] {market}: "
                        f"POC={_vp.poc_price:,.0f} "
                        f"VAH={_vp.vah:,.0f} VAL={_vp.val:,.0f} RR={_rr:.2f}"
                    )
                    # POC 컨텍스트 캐시 저장 (진입 신뢰도 부스트용)
                    if not hasattr(self, "_vp_cache"):
                        self._vp_cache = {}
                    self._vp_cache[market] = {
                        "poc": float(_vp.poc_price),
                        "vah": float(_vp.vah),
                        "val": float(_vp.val),
                        "rr":  float(_rr),
                        "price": float(_cur_price),
                    }
            except Exception as _ve:
                logger.info(f"[VolumeProfile]  ({market}): {_ve}")

            df_processed = await self.candle_processor.process(market, df_1h, "60")
            if df_processed is None:
                logger.info(f'[ANALYZE] {market} df_processed=None (CandleProcessor 실패)')
                return

            regime = self.regime_detector.detect(
                market, df_processed,
                fear_greed_index=self.fear_greed.index,
            )
            # [MDD-L1] regime 및 ADX 캐시 저장 (TRENDING_DOWN return 전에 먼저 저장)
            if not hasattr(self, "_last_regime_cache"):
                self._last_regime_cache = {}
            if not hasattr(self, "_adx_cache"):
                self._adx_cache = {}
            _regime_str = regime.value if hasattr(regime, "value") else str(regime)
            self._last_regime_cache[market] = _regime_str
            try:
                _adx_series = df_processed.get("adx", df_processed.get("ADX", None))
                if _adx_series is not None and len(_adx_series) > 0:
                    self._adx_cache[market] = float(_adx_series.iloc[-1])
                else:
                    self._adx_cache[market] = 0
            except Exception:
                self._adx_cache[market] = 0
            logger.debug(
                f"[MDD-L1] {market} 캐시저장 "
                f"regime={_regime_str} ADX={self._adx_cache.get(market,0):.1f} "
                f"FG={getattr(self.fear_greed,'index',50)}"
            )

            if regime == MarketRegime.TRENDING_DOWN:
                logger.info(f'[ANALYZE] {market} TRENDING_DOWN 차단 (regime={regime})')
                return
            if regime == MarketRegime.BEAR_REVERSAL:
                logger.info(
                    f" BEAR_REVERSAL  ({market}) → "
                    f"역발상 매수 탐색 (포지션 50% 축소)"
                )
                self._bear_reversal_markets = getattr(
                    self, "_bear_reversal_markets", set()
                )
                self._bear_reversal_markets.add(market)
            else:
                # [QUALITY-2 FIX] 속성명 _bear_reversal_markets로 통일
                self._bear_reversal_markets = getattr(
                    self, "_bear_reversal_markets", set()
                )
                self._bear_reversal_markets.discard(market)

            is_dumping, dump_reason = self.volume_spike.is_dumping(df_processed, market)
            _is_bear_rev = market in getattr(self, "_bear_reversal_markets", set())
            _in_pyramid  = getattr(self, "_current_pyramid_market", None) == market

            if is_dumping and not _is_bear_rev and not _in_pyramid:
                logger.info(f"  ({market}): {dump_reason}")
                return
            elif is_dumping and _is_bear_rev:
                logger.info(
                    f" BEAR_REVERSAL   ({market}): {dump_reason}"
                )

            signals  = await self._run_strategies(market, df_processed)
            # [VP2-PATCH] 전략 신호 상세 디버그
            if signals:
                for _dbg_s in signals:
                    logger.debug(f"[STRATEGY-SIG] {market} | {_dbg_s.strategy_name} | {_dbg_s.signal.name} | score={_dbg_s.score:.3f} conf={_dbg_s.confidence:.3f}")
            else:
                logger.info(f"[STRATEGY-NONE] {market} 전략 신호 0개 — df_processed 행수={len(df_processed) if df_processed is not None else 0}")
            ml_pred  = await self._get_ml_prediction(market, df_processed)
            ppo_pred = await self._get_ppo_prediction(market, df_processed)

            if ppo_pred and ml_pred:
                ml_conf  = ml_pred.get("confidence",  0)
                ppo_conf = ppo_pred.get("confidence", 0)
                ml_sig   = ml_pred.get("signal", "HOLD")
                ppo_sig  = str(ppo_pred.get("action", ppo_pred.get("signal", "HOLD"))).upper()
                if ml_sig == ppo_sig:
                    # Agreement: boost confidence but never reduce below ml_conf
                    boosted = ml_conf * 0.7 + ppo_conf * 0.3 + 0.05
                    ml_pred["confidence"]    = min(1.0, max(ml_conf, boosted))
                    ml_pred["ppo_agreement"] = True
                else:
                    # Disagreement: keep ML confidence unchanged (no penalty)
                    ml_pred["confidence"]    = ml_conf
                    ml_pred["ppo_agreement"] = False
                logger.info(
                    f"ML+PPO  ({market}): "
                    f"ML={ml_pred.get('signal','?')}({ml_conf:.2f}) | "
                    f"PPO={ppo_pred.get('action','?')}({ppo_conf:.2f}) | "
                    f"일치={ml_pred.get('ppo_agreement', False)}"
                )
            elif ppo_pred and ml_pred is None:
                ml_pred = ppo_pred

            fg_adj = self.fear_greed.get_signal_adjustment()
            if fg_adj.get("block_buy", False):
                logger.info(
                    f"   ({market}): "
                    f"지수={self.fear_greed.index} ({self.fear_greed.label})"
                )
                return
            if ml_pred and fg_adj.get("mode") == "suppressed":
                if ml_pred.get("confidence", 0) < 0.35:
                    logger.info(
                        f"  ({market}): "
                        f"지수={self.fear_greed.index} ({self.fear_greed.label})"
                    )
                    return

            spike_info          = self.volume_spike.detect(df_processed, market)
            vol_confidence_adj  = self.volume_spike.get_confidence_adjustment(spike_info)

            combined = self.signal_combiner.combine(
                signals, market, ml_pred, regime.value
            )
            # [FIX] confidence 보정: ML confidence를 combined에 반영
            if combined is not None and ml_pred is not None:
                _ml_conf = ml_pred.get('confidence', 0.0)
                if combined.confidence < _ml_conf:
                    combined.confidence = _ml_conf
                    logger.info(f'[ANALYZE] {market} confidence 보정: 0.0→{_ml_conf:.3f}')

            if combined is None:
                logger.info(f'[ANALYZE] {market} combined=None → BEAR_REVERSAL 체크')
                if self.portfolio.position_count >= self.settings.trading.max_positions:
                    return
                if market in getattr(self, "_bear_reversal_markets", set()):
                    _today           = datetime.now().strftime("%Y-%m-%d")
                    _bear_count_key  = f"_bear_rev_count_{_today}"
                    _bear_count      = getattr(self, _bear_count_key, 0)
                    if _bear_count >= 6:
                        logger.info(
                            f" BEAR_REVERSAL    ({market}): "
                            f"{_bear_count}/6 → 강제 BUY 차단"
                        )
                        return
                    _max_p = self.settings.trading.max_positions
                    if self.portfolio.position_count >= int(_max_p * 0.5):
                        logger.info(
                            f" BEAR_REVERSAL  50%  ({market}): "
                            f"{self.portfolio.position_count}/"
                            f"{int(_max_p*0.5)} → 차단"
                        )
                        return
                    if hasattr(self, "_sl_cooldown") and market in self._sl_cooldown:
                        import datetime as _dt2
                        if _dt2.datetime.now() < self._sl_cooldown[market]:
                            remaining = int(
                                (
                                    self._sl_cooldown[market]
                                    - _dt2.datetime.now()
                                ).total_seconds() // 60
                            )
                            logger.info(
                                f"    ({market}): "
                                f"{remaining}분 후 재매수 가능"
                            )
                            return
                        else:
                            del self._sl_cooldown[market]
                    _fg_idx = getattr(self.fear_greed, "index", 50)
                    if _fg_idx > 25:  # [FIX] 21→25 완화
                        logger.info(
                            f" BEAR_REVERSAL    ({market}): "
                            f"지수={_fg_idx} > 20 → 강제 BUY 차단"
                        )
                        return
                    setattr(self, _bear_count_key, _bear_count + 1)
                    logger.info(
                        f" BEAR_REVERSAL  BUY   ({market}): "
                        f"오늘 {_bear_count+1}/6회"
                    )
                    combined = CombinedSignal(
                        market=market,
                        signal_type=SignalType.BUY,
                        score=0.63,
                        confidence=0.63,
                        agreement_rate=1.0,
                        contributing_strategies=["BEAR_REVERSAL"],
                        reasons=["극단적 공포 역발상 매수"],
                    )

            if combined is None:
                logger.info(f'[ANALYZE] {market} combined=None (최종 신호 없음) 종료')
                return

            if vol_confidence_adj > 0:
                combined.confidence = min(
                    1.0, combined.confidence * (1 + vol_confidence_adj)
                )
                logger.info(
                    f"   ({market}): "
                    f"+{vol_confidence_adj:.2%} 신뢰도 향상"
                )

            ob_analyzer = getattr(self, "orderbook_analyzer", None)
            if ob_analyzer is not None:
                try:
                    ob_data    = self.cache_manager.get_orderbook(market)
                    ob_signal  = ob_analyzer.analyze(market, ob_data)
                    can_buy_ob, ob_reason = ob_analyzer.can_buy(ob_signal)
                    if not can_buy_ob and combined.signal_type == SignalType.BUY:
                        logger.info(f"  ({market}): {ob_reason}")
                        return
                    ob_adj = ob_analyzer.get_confidence_adjustment(
                        ob_signal, trade_side="BUY"
                    )
                    if abs(ob_adj) > 0.01:
                        combined.confidence = min(
                            1.0, combined.confidence * (1 + ob_adj)
                        )
                        logger.info(
                            f"  ({market}): {ob_adj:+.2%} "
                            f"→ 신뢰도={combined.confidence:.2f}"
                        )
                except Exception as ob_e:
                    logger.info(f"   ({market}): {ob_e}")
            else:
                logger.info(f"   ({market}) → 통과")

            can_buy_news, news_reason = self.news_analyzer.can_buy(market)
            if not can_buy_news and combined.signal_type == SignalType.BUY:
                logger.info(f"   ({market}): {news_reason}")
                return

            news_score, news_boost = self.news_analyzer.get_signal_boost(market)
            if abs(news_boost) > 0.3:
                original_score = combined.score
                combined.score = combined.score + news_boost  # EB-7: 부정=음수이므로 +가 맞음
                logger.info(
                    f"   ({market}): "
                    f"{original_score:.2f} → {combined.score:.2f} "
                    f"(boost={news_boost:+.2f}, 감성={news_score:+.3f})"
                )

            log_signal(
                market, combined.signal_type.name,
                combined.score, combined.contributing_strategies
            )

            if self.mtf_merger is not None:
                try:
                    _tf_map = {
                        "1d":  ("day",       "1d"),
                        "4h":  ("minute240", "4h"),
                        "1h":  ("minute60",  "1h"),
                        "15m": ("minute15",  "15m"),
                        "5m":  ("minute5",   "5m"),
                        "1m":  ("minute1",   "1m"),
                    }
                    _tf_data = {}
                    for _tf_key, (_tf_upbit, _tf_cache) in _tf_map.items():
                        # 1) 1h는 이미 처리된 df_processed 재사용 (API 호출 0)
                        if _tf_key == "1h" and df_processed is not None and len(df_processed) >= 5:
                            _tf_data["1h"] = df_processed
                            continue
                        # 2) 5m/1m 은 Rate Limit 절약을 위해 skip
                        if _tf_key in ("5m", "1m"):
                            continue
                        # 3) cache_manager에서 다양한 방법으로 시도
                        _cached = None
                        for _getter in [
                            lambda: self.cache_manager.get_ohlcv(market, _tf_key),
                            lambda: self.cache_manager.get_ohlcv(market, _tf_cache),
                            lambda: self.cache_manager.get_candles(market, _tf_cache),
                            lambda: self.cache_manager.get_candles(market, _tf_key),
                        ]:
                            try:
                                _cached = _getter()
                                if _cached is not None and len(_cached) >= 5:
                                    break
                                _cached = None
                            except Exception:
                                _cached = None
                        if _cached is not None and len(_cached) >= 5:
                            _tf_data[_tf_key] = _cached
                            continue
                        # 4) REST API fallback (1d, 4h 만 추가 요청)
                        if _tf_key in ("1d", "4h"):
                            try:
                                _fetched = await self.rest_collector.get_ohlcv(
                                    market, _tf_upbit, 60
                                )
                                if _fetched is not None and len(_fetched) >= 5:
                                    _tf_data[_tf_key] = _fetched
                            except Exception as _e:
                                logger.debug(f"[P10-PATCH][MTF-WARN] {market} {_tf_key} 조회 실패: {_e}")

                    if _tf_data:
                        _mtf_result = self.mtf_merger.analyze(_tf_data)
                        _mtf_score  = _mtf_result.combined_score
                        _mtf_dir    = _mtf_result.final_direction.value

                        if combined.signal_type == SignalType.BUY:
                            if _mtf_dir <= -1 and not _is_bear_rev:
                                logger.info(
                                    f" MTF  ({market}): "
                                    f"방향={_mtf_result.final_direction.name} | "
                                    f"{_mtf_result.reason}"
                                )
                                return
                            if _mtf_dir >= 1:
                                _boost = min(0.3, abs(_mtf_score) * 0.2)
                                combined.score = min(3.0, combined.score + _boost)
                                logger.info(
                                    f" MTF   ({market}): "
                                    f"+{_boost:.2f} → score={combined.score:.2f} | "
                                    f"TF수={len(_tf_data)}개 | {_mtf_result.reason}"
                                )
                            else:
                                logger.info(
                                    f"MTF  ({market}): {_mtf_result.reason}"
                                )
                        elif combined.signal_type == SignalType.SELL:
                            if _mtf_dir >= 1:
                                logger.info(
                                    f"MTF SELL  ({market}): "
                                    f"상위TF 상승중 | {_mtf_result.reason}"
                                )
                except Exception as _mtf_e:
                    logger.info(f"MTF   ({market}): {_mtf_e}")

            try:
                await self.db_manager.log_signal({
                    "market":      market,
                    "signal_type": combined.signal_type.name,
                    "score":       combined.score,
                    "confidence":  combined.confidence,
                    "strategies":  combined.contributing_strategies,
                    "regime":      getattr(combined, "regime", ""),
                    "executed":    False,
                })
            except Exception as _sig_e:
                logger.info(f"signal_log DB  : {_sig_e}")

            _is_bear_rev = market in getattr(self, "_bear_reversal_markets", set())
            if _is_bear_rev and combined.signal_type != SignalType.SELL:
                if combined.signal_type != SignalType.BUY:
                    logger.info(
                        f" BEAR_REVERSAL   ({market}): "
                        f"{combined.signal_type.name} → BUY "
                        f"(score={combined.score:.2f})"
                    )
                    combined.signal_type = SignalType.BUY
                    combined.score       = max(combined.score, 0.45)
                    combined.confidence  = max(combined.confidence, 0.45)
                combined.bear_reversal = True

            try:
                _ob_df = self.cache_manager.get_candles(market, "1h")
                if _ob_df is not None and len(_ob_df) >= 30:
                    _ob_price = float(df_processed["close"].iloc[-1])
                    _ob_sig   = self.ob_detector.detect(_ob_df, _ob_price)
                    if (
                        _ob_sig.signal == "SELL_ZONE"
                        and _ob_sig.confidence >= 0.5
                    ):
                        if combined.signal_type == SignalType.BUY:
                            logger.info(
                                f"  SELL_ZONE   ({market}): "
                                f"신뢰도={_ob_sig.confidence:.2f} "
                                f"거리={_ob_sig.dist_bearish_pct:.1f}%"
                            )
                            return
                    if (
                        _ob_sig.signal == "BUY_ZONE"
                        and _ob_sig.confidence >= 0.4
                    ):
                        logger.info(
                            f"  BUY_ZONE ({market}): "
                            f"신뢰도={_ob_sig.confidence:.2f} "
                            f"거리={_ob_sig.dist_bullish_pct:.1f}%"
                        )
            except Exception as _ob_e:
                logger.info(f"   ({market}): {_ob_e}")

            if combined.signal_type == SignalType.BUY:
                # [FIX-QUOTA-CHECK] 전략별 쿼터 체크
                _strat_list_q = getattr(combined, 'contributing_strategies', []) or []  # [FIX-SIGNAL-REF]
                _strat_name_q = _strat_list_q[0] if _strat_list_q else 'default'
                _quota_map_q  = getattr(self, '_strategy_quota', {})
                _strat_quota  = _quota_map_q.get(_strat_name_q, 999)
                _strat_open   = sum(
                    1 for _pq in self.portfolio.open_positions.values()
                    if _strat_name_q in str(getattr(_pq, 'strategy', ''))
                )
                if _strat_quota < 999 and _strat_open >= _strat_quota:
                    logger.debug(
                        f'[QUOTA-BLOCK] {market} {_strat_name_q} '
                        f'쿼터 초과 ({_strat_open}/{_strat_quota}) → 스킵'
                    )
                    self._buying_markets.discard(market)
                    return
                if market not in self.portfolio.open_positions:
                    # ── 전략별 쿨다운 체크 ──────────────────────────────────────
                    try:
                        import datetime as _ecd_dt
                        _cd_map = getattr(self, '_strat_cooldown_until', {})
                        _cd_now = _ecd_dt.datetime.now()
                        _sig_strat = getattr(combined, 'strategy_name', '') or ''
                        _is_cd = any(
                            _cd_now < _exp
                            for _k, _exp in _cd_map.items()
                            if _k in _sig_strat or _sig_strat in _k
                        )
                        if _is_cd:
                            _cd_key = next(
                                (_k for _k, _exp in _cd_map.items()
                                 if (_cd_now < _exp) and (_k in _sig_strat or _sig_strat in _k)),
                                'unknown'
                            )
                            _cd_remain = max(0, int((_cd_map[_cd_key] - _cd_now).total_seconds() // 60))
                            logger.info(
                                f'[STRAT-CD] {market} {_sig_strat} 냉각 중 '
                                f'({_cd_remain}분 남음) -> 매수 스킵'
                            )
                            self._buying_markets.discard(market)
                            return
                    except Exception:
                        pass
                    # ── 쿨다운 체크 끝 ──────────────────────────────────────────
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
                        await self._execute_buy(market, combined, df_processed)
                    # [FIX] BUY 시 쿨다운 갱신 제거
                    try:
                        _sig_type_str = str(getattr(combined, 'signal_type', ''))  # [FIX-SIGNAL-REF]
                    except Exception:
                        _sig_type_str = ''
                    if 'BUY' not in _sig_type_str.upper():
                        self._last_signal_time[market] = time.time()
                else:
                    logger.info(
                        f"   ({market}) → 중복 매수 스킵"
                    )

        except Exception as e:
            logger.info(f'[ANALYZE] {market} 예외 발생: {e}')
            logger.error(f"   ({market}): {e}")

    # ── 전략 선택 / 실행 ────────────────────────────────────────

    def _get_preferred_strategies(self, market: str) -> list:
        BEAR_PREFERRED = {
            "KRW-BTC":  ["MACD_Cross",       "Supertrend"],
            "KRW-ETH":  ["Bollinger_Squeeze", "MACD_Cross", "ATR_Channel"],  # [U6-PATCH]
            "KRW-XRP":  ["Bollinger_Squeeze", "MACD_Cross", "ATR_Channel"],  # [U6-PATCH]
            "KRW-SOL":  ["Bollinger_Squeeze", "MACD_Cross", "Supertrend"],  # [U6-PATCH]
            "KRW-ADA":  ["Bollinger_Squeeze", "MACD_Cross", "ATR_Channel"],  # [U6-PATCH]
            "KRW-DOGE": ["Bollinger_Squeeze", "MACD_Cross"],
            "KRW-DOT":  ["Bollinger_Squeeze", "MACD_Cross", "ATR_Channel"],  # [U6-PATCH]
            "KRW-LINK": ["Bollinger_Squeeze", "MACD_Cross", "ATR_Channel"],  # [U6-PATCH]
            "KRW-AVAX": ["Bollinger_Squeeze", "MACD_Cross", "Supertrend"],  # [U6-PATCH]
            "KRW-ATOM": ["Bollinger_Squeeze", "MACD_Cross", "ATR_Channel"],  # [U6-PATCH]
        }
        BULL_PREFERRED = {
            "KRW-BTC":  ["MACD_Cross",       "Supertrend"],
            "KRW-ETH":  ["Supertrend"],  # [ST-1] VWAP_Reversion 제거
            "KRW-XRP":  ["Supertrend",        "MACD_Cross"],
            "KRW-SOL":  ["Supertrend",        "MACD_Cross"],
            "KRW-ADA":  ["Supertrend",        "Bollinger_Squeeze"],
            "KRW-DOGE": ["Bollinger_Squeeze", "MACD_Cross"],
            "KRW-DOT":  ["Supertrend"],  # [ST-1] VWAP_Reversion 제거
            "KRW-LINK": ["Supertrend"],  # [ST-1] VWAP_Reversion 제거
            "KRW-AVAX": ["Supertrend"],  # [ST-1] VWAP_Reversion 제거
            "KRW-ATOM": ["Supertrend"],  # [ST-1] VWAP_Reversion 제거
        }
        is_bull   = market not in getattr(self, "_bear_reversal_markets", set())
        preferred = (BULL_PREFERRED if is_bull else BEAR_PREFERRED).get(
            market, list(self._strategies.keys())
        )
        available = [n for n in preferred if n in self._strategies]
        if not available:
            available = list(self._strategies.keys())
        return available


    async def _run_strategies(self, market: str, df) -> list:
        signals   = []
        tasks     = []
        preferred = self._get_preferred_strategies(market)
        selected  = {n: s for n, s in self._strategies.items() if n in preferred}
        if not selected:
            selected = self._strategies
        logger.debug(
            f"  ({market}): {list(selected.keys())} "
            f"[전체 {len(self._strategies)}개 중 {len(selected)}개]"
        )
        # [TIME-FILTER] 새벽 00~06시 저유동성 구간 Order_Block 차단
        from datetime import datetime as _dt_tf, timezone, timedelta
        _KST = timezone(timedelta(hours=9))
        _now_hour = _dt_tf.now(_KST).hour

        # [OPT] 시간대별 포지션 크기 배율
        _time_cfg = getattr(self.settings, 'time_size_boost', {})
        if 12 <= _now_hour < 18:
            _time_size_mult = 1.20   # 오후 최적 시간대
        elif 0 <= _now_hour < 6:
            _time_size_mult = 0.70   # 새벽 축소
        else:
            _time_size_mult = 1.00   # 기본

        if 0 <= _now_hour < 6:
            _ob_names = {"Order_Block", "VolBreakout", "Vol_Breakout"}
            selected  = {n: s for n, s in selected.items() if n not in _ob_names}
            if not selected:
                logger.info(
                    f"[TIME-FILTER] {market} 새벽({_now_hour}시) "
                    f"Order_Block 단독 → 전략 없음 차단"
                )
                return []
            logger.debug(
                f"[TIME-FILTER] {market} 새벽({_now_hour}시) "
                f"Order_Block 제외 후 잔여전략: {list(selected.keys())}"
            )

        # [MDD-L1] Vol_Breakout / ML_Ensemble 레짐 필터
        _fg_now   = getattr(self.fear_greed, "index", 50) or 50
        _regime_now = getattr(self, "_last_regime_cache", {}).get(market, None)
        _adx_now    = getattr(self, "_adx_cache", {}).get(market, 0)

        # [ST-4] Vol_Breakout 영구 차단: DB -₩3,521, 29% 승률
        import os as _os
        from datetime import datetime as _dtnow, timedelta as _td
        _live_start_str = _os.getenv("LIVE_START_DATE", "")
        _vol_breakout_live_blocked = False
        if _live_start_str and getattr(self.settings, "mode", "paper") == "live":
            try:
                _live_start = _dtnow.fromisoformat(_live_start_str)
                _days_since_live = (_dtnow.now() - _live_start).days
                if _days_since_live < 30:
                    _vol_breakout_live_blocked = True
                    logger.debug(
                        f"[LIVE-SAFE] Vol_Breakout 실거래 초기 차단 "
                        f"(실거래 {_days_since_live}일차 < 30일)"
                    )
            except Exception as _e:
                import logging as _lg
                _lg.getLogger("engine_buy").debug(f"[WARN] engine_buy 오류 무시: {_e}")
                pass

        _filtered = {}
        for name, strategy in selected.items():
            # [ST-4] Vol_Breakout 영구 차단: DB -₩3,521, 29% 승률
            if name in ("Vol_Breakout", "VolBreakout", "volatility_break"):
                if _vol_breakout_live_blocked:
                    logger.info(
                        f"[LIVE-SAFE] {market} Vol_Breakout 완전 차단 "
                        f"(실거래 초기 30일 안전모드)"
                    )
                    continue
            # [FIX-VOLBREAK] Vol_Breakout 완전 비활성화 (승률 29%, 기대값 -0.270%)
            if name in ("Vol_Breakout", "VolBreakout", "volatility_break"):
                logger.debug(f"[VOL-DISABLED] {market} Vol_Breakout 영구 차단")
                continue
            # ML_Ensemble: 누적 거래 30건 미만이면 등록만 하고 나중에 크기 50% 축소

            # [REGIME-MATRIX] 레짐별 전략 허용 매트릭스
            # [C-4 FIX] MarketRegime.value 정규화
            # 실제 반환값: TRENDING_UP, TRENDING_DOWN, RANGING,
            #              VOLATILE, BEAR_REVERSAL, UNKNOWN
            _regime_val = (
                _regime_now.value
                if hasattr(_regime_now, 'value')
                else str(_regime_now or 'UNKNOWN')
            )
            _REGIME_MATRIX = {
                'MACD_Cross':        {
                    'TRENDING_UP': True,  'RANGING': False,
                    'VOLATILE':    False, 'TRENDING_DOWN': False,
                    'BEAR_REVERSAL': False, 'UNKNOWN': True,
                },
                'Supertrend':        {
                    'TRENDING_UP': True,  'RANGING': False,
                    'VOLATILE':    False, 'TRENDING_DOWN': False,
                    'BEAR_REVERSAL': False, 'UNKNOWN': True,
                },
                'VWAP_Reversion':    {
                    'TRENDING_UP': True,  'RANGING': True,
                    'VOLATILE':    False, 'TRENDING_DOWN': False,
                    'BEAR_REVERSAL': True,  'UNKNOWN': True,
                },
                'RSI_Divergence':    {
                    'TRENDING_UP': True,  'RANGING': True,
                    'VOLATILE':    True,  'TRENDING_DOWN': True,
                    'BEAR_REVERSAL': True,  'UNKNOWN': True,
                },
                'Bollinger_Squeeze': {
                    'TRENDING_UP': True,  'RANGING': True,
                    'VOLATILE':    True,  'TRENDING_DOWN': False,
                    'BEAR_REVERSAL': True,  'UNKNOWN': True,
                },
                'ATR_Channel':       {
                    'TRENDING_UP': True,  'RANGING': True,
                    'VOLATILE':    True,  'TRENDING_DOWN': False,
                    'BEAR_REVERSAL': False, 'UNKNOWN': True,
                },
                'OrderBlock_SMC':    {
                    'TRENDING_UP': True,  'RANGING': True,
                    'VOLATILE':    False, 'TRENDING_DOWN': False,
                    'BEAR_REVERSAL': False, 'UNKNOWN': True,
                },
                'VolBreakout':       {
                    'TRENDING_UP': True,  'RANGING': False,
                    'VOLATILE':    False, 'TRENDING_DOWN': False,
                    'BEAR_REVERSAL': False, 'UNKNOWN': False,
                },
            }
            if name in _REGIME_MATRIX and _regime_now is not None:
                # dict.get()으로 단순화 — 없는 레짐은 True(허용)
                _allowed = _REGIME_MATRIX[name].get(_regime_val, True)
                if not _allowed:
                    logger.debug(
                        f"[REGIME-MATRIX] {market} {name} 차단 "
                        f"(regime={_regime_now})"
                    )
                    continue

            _filtered[name] = strategy
        selected = _filtered

        for name, strategy in selected.items():
            tasks.append(asyncio.get_running_loop().run_in_executor(
                None, strategy.analyze, market, df, {}
            ))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.debug(
                    f"   ({market}): "
                    f"{type(result).__name__}: {result}"
                )
            elif result:
                signals.append(result)
                logger.debug(
                    f"   ({market}): "
                    f"signal={getattr(result,'signal','?')} "
                    f"score={getattr(result,'score',0):.2f} "
                    f"strategy={getattr(result,'strategy_name','?')}"
                )
        if not signals:
            logger.debug(
                f"   ({market}): "
                f"0/{len(selected)}개 전략에서 신호 없음"
            )
        return signals

    # ── ML / PPO 예측 ────────────────────────────────────────────

    async def _evaluate_entry_signals(self, market: str, df, ml_score: float):
        # ── 글로벌 레짐 체크 ────────────────────────────────────
        global_regime = getattr(self, "_global_regime", GlobalRegime.UNKNOWN)
        policy = self.global_regime_detector.get_policy(global_regime)

        # 급등 여부 확인
        surge_info  = getattr(self, "_surge_cache", {}).get(market, {})
        is_surge    = surge_info.get("is_surge", False) and surge_info.get("score", 0) >= 0.35  # [FIX] 0.6->0.35 score scale unified
        surge_grade = surge_info.get("grade", "")
        surge_score = surge_info.get("score", 0.0)
        # 일반 매수 차단 (BEAR/BEAR_WATCH)
        if not policy["allow_normal_buy"] and not is_surge:
            logger.info(
                f"[GlobalRegime] {market} 매수 차단 | "
                f"레짐={global_regime.value} | 급등아님"
            )
            return None

        # 급등도 차단 (UNKNOWN)
        if not policy["allow_surge_buy"]:
            return None

        # ML 임계값 레짐별 동적 조정
        # [ML-BYPASS] ML 모델 미로드 상태 → min_ml_score 체크 비활성화
        # 기술적 전략 신호만으로 진입 결정
        effective_ml_score = policy["min_ml_score"]  # 참조만 유지
        # if ml_score < effective_ml_score: → 비활성화
        logger.debug(
            f"[ML-BYPASS] {market} ML점수={ml_score:.3f} "
            f"(임계값={effective_ml_score:.3f} 우회됨)"
        )
        try:
            # 1. ATR 변동성 필터 (v2.1.0)
            if 'atr' in df.columns and df['atr'].iloc[-1] is not None and df['atr'].iloc[-1] > 0:
                atr = df['atr'].iloc[-1]
            else:
                # ATR 없으면 고가-저가 범위의 2% 추정
                if 'high' in df.columns and 'low' in df.columns:
                    recent_range = (df['high'].iloc[-14:].mean() - df['low'].iloc[-14:].mean())
                    atr = recent_range
                    logger.info(f"{market} ATR   →  : {atr:.2f}")
                else:
                    atr = df['close'].iloc[-1] * 0.02  # 폴백: 현재가의 2%
                    logger.info(f"{market} ATR :  2%")
            
            price = df['close'].iloc[-1]
            volatility = (atr / price) * 100 if price > 0 else 0
            # ── ATR 필터: 급등 코인 우회 ─────────────────────────
            if volatility < 0.5 or volatility > 5.0:
                if is_surge:
                    logger.info(
                        f"[Surge] {market} ATR 우회 | "
                        f"vol={volatility:.2f}% grade={surge_grade} score={surge_score:.3f}"
                    )
                else:
                    logger.info(f"{market} ATR 필터: {volatility:.2f}%")
                    return None

            
            # 2. VolumeProfile RR 필터 (v2.1.0)
            try:
                if hasattr(self, 'volume_profile') and hasattr(self.volume_profile, 'calculate'):
                    vp_result = self.volume_profile.calculate(df)
                    vp_rr = vp_result.get('rr', 0) if isinstance(vp_result, dict) else 0
                else:
                    vp_rr = 999  # VolumeProfile 없으면 통과
            except Exception as e:
                logger.info(f'{market} VolumeProfile  : {e}')
                vp_rr = 999  # 에러 시 통과
            if vp_rr < -0.5:  # RR -0.5 미만은 차단 (저항 직전 진입 방지)
                logger.info(f"{market} VolumeProfile RR 차단: {vp_rr:.2f}")
                return None
            
            # 3. Multi-Timeframe Confirmation (v2.1.0)
            if hasattr(self, 'mtf_confirmation'):
                mtf_result = await self.mtf_confirmation.check(market, df)
                if not mtf_result.get("aligned", False):
                    # [FIX] SURGE 진입 시 MTF 미정렬 우회
                    if not is_surge:
                        logger.info(f"{market} MTF 미정렬 → 진입 차단")
                        return None
                    else:
                        logger.info(f"[SURGE] {market} MTF 미정렬 → SURGE이므로 우회")
            
            # Kelly Criterion 포지션 크기 계산
            # 6. Kelly Criterion 포지션 크기 (v2.1.0)
            win_rate = getattr(self, 'historical_win_rate', 0.55)
            avg_win = getattr(self, 'avg_win', 0.03)
            avg_loss = getattr(self, 'avg_loss', 0.02)
            
            if avg_loss > 0:
                kelly_fraction = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
                kelly_fraction = max(0.05, min(kelly_fraction, 0.15))  # 5~15% 제한
            else:
                kelly_fraction = 0.10
            
            logger.info(
                f" {market}    | ML: {ml_score:.3f} | "
                f"Kelly: {kelly_fraction:.1%} | ATR: {volatility:.2f}% | RR: {vp_rr:.2f}"
            )
            
            return {
                'action': 'BUY',
                'confidence': ml_score,
                'position_size': kelly_fraction,
                
                'filters_passed': ['ATR', 'VolumeProfile', 'MTF', 'ML', 'Consensus']
            }
            
        except Exception as e:
            logger.error(f"{market}   : {e}")
            logger.info(f"{market} 진입평가 예외 발생 → unknown")
            return None


    async def _execute_buy(self, market: str, signal: CombinedSignal, df):
        # [FIX-DUP] 중복 매수 방지 — Race Condition 완전 차단
        # ── [PHASE2] 저성과 전략 진입 차단 ─────────────────────
        _BLOCKED = {"Vol_Breakout", "unknown", ""}
        _sig_strat = getattr(signal, "strategy_name", "") or ""
        if not _sig_strat or _sig_strat in _BLOCKED:
            logger.debug(f"[BLOCKED] {market} 전략={_sig_strat!r} 차단됨 → 스킵")
            return
        # ────────────────────────────────────────────────────────
        if not hasattr(self, '_buying_markets'):
            self._buying_markets = set()
        if market in self._buying_markets:
            logger.debug(f'[DUP-GUARD] {market}: 매수 진행 중 → 스킵')
            return
        if self.portfolio.has_position(market):
            logger.debug(f'[DUP-GUARD] {market}: 포지션 이미 존재 → 스킵')
            return
        self._buying_markets.add(market)  # [DUP-GUARD] 선점 등록
        try:
            _max_pos = self.settings.trading.max_positions
            if not hasattr(self, "_sell_cooldown"):
                self._sell_cooldown = {}
            _cd_last = self._sell_cooldown.get(market)
            if (_cd_last is not None and
                    (datetime.now() - _cd_last).total_seconds() < 1200):
                _cd_remain = 1200 - (datetime.now() - _cd_last).total_seconds()
                logger.info(f'[COOLDOWN] {market}: 매도 후 {_cd_remain:.0f}초 남음 → BUY 차단')
                return

            # [FIX] DB 기반 손절 후 재매수 금지 체크 (재시작 후에도 유지)
            try:
                import datetime as _dt_slcd
                _slcd_key = f"sl_cooldown_{market}"
                _slcd_state = await self.db_manager.get_state(_slcd_key)
                if _slcd_state:
                    _ban_until = _dt_slcd.datetime.fromisoformat(str(_slcd_state))
                    if _dt_slcd.datetime.now() < _ban_until:
                        _remain_min = int((_ban_until - _dt_slcd.datetime.now()).total_seconds() // 60)
                        logger.info(
                            f"[SL-BAN] {market}: 손절 후 재매수 금지 "
                            f"({_remain_min}분 남음 / 해제={_ban_until.strftime('%H:%M')})"
                        )
                        return
                    else:
                        # 쿨다운 만료 → DB 삭제
                        await self.db_manager.delete_state(_slcd_key)
            except Exception as _slcd_e:
                logger.debug(f"[SL-BAN] {market} 체크 오류: {_slcd_e}")

            if self.portfolio.position_count >= _max_pos:
                logger.info(
                    f"   ({market}): "
                    f"{self.portfolio.position_count}/{_max_pos} → 매수 취소"
                )
                return
            if self.portfolio.is_position_open(market):
                logger.debug(f"    ({market}): 이미 포지션 존재")
                return
            # [FIX-DUP] L1054 중복 체크 제거 (L1013에서 이미 선점 등록됨)
            # [FIX B] ML=SELL 신호이면 매수 차단
            _ml_pred_b = self._ml_predictions.get(market, {})
            if isinstance(_ml_pred_b, dict):
                _ml_sig_b  = _ml_pred_b.get("signal", "HOLD")
                _ml_conf_b = float(_ml_pred_b.get("confidence", 0))
                _is_bear_rev_b = market in getattr(self, "_bear_reversal_markets", set())
                _is_surge_signal = (
                    hasattr(signal, "contributing_strategies") and
                    "SURGE_FASTENTRY" in (signal.contributing_strategies or [])
                )
                # [ML-VETO v2] PPO 동의 시 임계값 추가 강화
                _ppo_pred_veto = self._ppo_predictions.get(market, {}) if hasattr(self, "_ppo_predictions") else {}
                _ppo_sig_veto  = str(_ppo_pred_veto.get("action", "HOLD")).upper() if _ppo_pred_veto else "HOLD"
                _veto_thr = 0.55 if _ppo_sig_veto == "SELL" else 0.60
                if (_ml_sig_b == "SELL" and _ml_conf_b >= _veto_thr
                        and not _is_bear_rev_b and not _is_surge_signal):
                    logger.warning(
                        f"[ML-VETO] {market}: ML=SELL({_ml_conf_b:.2f}) "
                        f"PPO={_ppo_sig_veto} thr={_veto_thr:.2f} → BUY 차단"
                    )
                    self._buying_markets.discard(market)
                    return
                elif _is_surge_signal and _ml_sig_b == "SELL":
                    logger.info(
                        f"[ML-BLOCK-BYPASS] {market}: SURGE 진입 → ML=SELL 차단 우회"
                    )
            # EB-5: _sell_cooldown L2 중복 제거 (메서드 초입 L1에서 이미 처리)
            # L1 체크: 메서드 상단 _cd_last 블록에서 완료됨

            _symbol    = market.replace("KRW-", "")

            # [ML-FRESH] 매수 직전 ML 신선도 검증 – 5분 초과 시 재추론
            if not hasattr(self, "_ml_pred_times"):
                self._ml_pred_times = {}
            _ml_age = time.time() - self._ml_pred_times.get(market, 0)
            if _ml_age > 300:  # 5분 초과
                try:
                    _fresh_ml = await self._get_ml_prediction(market, df)
                    if _fresh_ml:
                        if not hasattr(self, "_ml_predictions"):
                            self._ml_predictions = {}
                        self._ml_predictions[market] = _fresh_ml
                        self._ml_pred_times[market]  = time.time()
                        _ml_sig_fresh  = _fresh_ml.get("signal", "HOLD")
                        _ml_conf_fresh = float(_fresh_ml.get("confidence", 0))
                        _veto_thr_f    = 0.60
                        if (_ml_sig_fresh == "SELL"
                                and _ml_conf_fresh >= _veto_thr_f
                                and market not in getattr(self, "_bear_reversal_markets", set())):
                            logger.warning(
                                f"[ML-FRESH-VETO] {market}: "
                                f"재추론 ML=SELL({_ml_conf_fresh:.2f}) → BUY 최종 차단"
                            )
                            self._buying_markets.discard(market)
                            return
                        logger.debug(
                            f"[ML-FRESH] {market}: 재추론 완료 "
                            f"ML={_ml_sig_fresh}({_ml_conf_fresh:.2f}) "
                            f"(경과={_ml_age:.0f}s)"
                        )
                except Exception as _mf_e:
                    logger.debug(f"[ML-FRESH] {market} 재추론 실패: {_mf_e}")

            _can_buy, _buy_note = self._wallet.can_buy(_symbol)
            if not _can_buy:
                logger.warning(f" SmartWallet  : {_buy_note}")
                self._buying_markets.discard(market)
                return
            logger.info(f" SmartWallet: {_buy_note}")

            krw = await self.adapter.get_balance("KRW")
            # EB-1: 잔고가 MIN_ORDER_KRW 미만이면 진입 차단 (소액 잔여금 주문 방지)
            _min_krw_eb1 = getattr(self.position_sizer, "MIN_ORDER_KRW", 5_000)
            if krw < _min_krw_eb1:
                logger.warning(
                    f"[EB-1] {market} KRW 잔고 ₩{krw:,.0f} < "
                    f"최소 ₩{_min_krw_eb1:,} → 매수 취소 (잔여금 보호)"
                )
                self._buying_markets.discard(market)
                return
            can_buy, reason = await self.risk_manager.can_open_position(
                market, krw, self.portfolio.position_count,
                global_regime=getattr(self, "_global_regime", None),
            )
            if not can_buy:
                logger.info(f"  ({market}): {reason}")
                self._buying_markets.discard(market)
                return

            _is_bear_rev_signal = "BEAR_REVERSAL" in getattr(
                signal, "contributing_strategies", []
            )
            _is_surge_entry = (
                hasattr(signal, "contributing_strategies") and
                "SURGE_FASTENTRY" in (signal.contributing_strategies or [])
            )
            if not _is_bear_rev_signal and not _is_surge_entry:
                if getattr(signal, 'confidence', 0) < self.settings.risk.buy_signal_threshold:
                    logger.debug(
                        f"    ({market}): "
                        f"점수={getattr(signal, 'confidence', 0):.2f} < "
                        f"임계={self.settings.risk.buy_signal_threshold:.2f} (FGI조정 비활성화)"
                    )
                    self._buying_markets.discard(market)
                    return
            elif _is_surge_entry:
                logger.info(
                    f"[SURGE-THRESHOLD-BYPASS] {market}: SURGE 진입 → "
                    f"buy_signal_threshold 우회 (confidence={getattr(signal, 'confidence', 0):.3f})"
                )

            last = df.iloc[-1]
            try:
                # [FIX-SURGE-CALL] is_surge + local_regime 전달
                _local_regime_buy = getattr(signal, "regime", None)
                _sl_levels_buy = self.atr_stop.calculate(
                    df, float(last["close"]), market=market,
                    global_regime=getattr(self, "_global_regime", None),
                    is_surge=_is_surge_entry,
                    local_regime=_local_regime_buy,
                )
                atr         = _sl_levels_buy.atr
                stop_loss   = _sl_levels_buy.stop_loss
                _sl_cap_buy = 0.987 if _is_surge_entry else 0.983  # [FIX-BUY-SL-CAP] SURGE -1.3% / 일반 -1.7%
                stop_loss   = max(stop_loss, float(last["close"]) * _sl_cap_buy)
                take_profit = _sl_levels_buy.take_profit
                logger.info(
                    f" ATR-SL ({market}): "
                    f"SL={stop_loss:,.0f} ({_sl_levels_buy.sl_pct*100:.2f}%) | "
                    f"TP={take_profit:,.0f} ({_sl_levels_buy.tp_pct*100:.2f}%) | "
                    f"RR={_sl_levels_buy.rr_ratio:.2f} | ATR={atr:,.0f}"
                )
            except Exception as _atr_e:
                logger.warning(
                    f" ATR   ({market}): {_atr_e} → 고정비율 사용"
                )
                atr           = float(last["close"]) * 0.02
                _sl_cap_buy   = 0.987 if _is_surge_entry else 0.983  # [FIX-BUY-FALLBACK] SURGE -1.3% / 일반 -1.7%
                stop_loss     = max(
                    float(last["close"]) * (1 - self.settings.risk.atr_stop_multiplier * 0.01),
                    float(last["close"]) * _sl_cap_buy,
                )
                take_profit   = float(last["close"]) * (
                    1 + self.settings.risk.atr_target_multiplier * 0.01
                )

            _strategy_name = getattr(signal, "contributing_strategies", ["default"])
            _strategy_name = _strategy_name[0] if _strategy_name else "default"
            # [FIX-KELLY] SURGE 신호는 confidence=score로 생성됨
            _is_surge_kelly = (
                hasattr(signal, "contributing_strategies") and
                "SURGE_FASTENTRY" in (signal.contributing_strategies or [])
            )
            _ml_conf = (
                getattr(signal, "confidence", 0.5)
                if _is_surge_kelly
                else getattr(signal, "ml_confidence", 0.5)
            )
            # [v2.1] consec_loss + atr_ratio 전달 → position_sizer 내부 통합 처리
            _consec_loss_ps = getattr(self, "_consecutive_loss_count", 0)
            try:
                _atr_val_ps  = float(df["atr"].iloc[-1]) if "atr" in df.columns else 0
                _atr_base_ps = float(df["close"].iloc[-1]) * 0.02
                _atr_ratio_ps = (_atr_val_ps / _atr_base_ps
                                 if _atr_base_ps > 0 and _atr_val_ps > 0
                                 else 1.0)
            except Exception:
                _atr_ratio_ps = 1.0
            _is_bear_rev_ps = getattr(signal, "bear_reversal", False)

            position_size = self.position_sizer.calculate(
                total_capital    = krw,
                strategy         = _strategy_name,
                market           = market,
                confidence       = _ml_conf,
                global_regime    = getattr(self, "_global_regime", None),
                consec_loss      = _consec_loss_ps,   # [v2.1] MDD-L2 이관
                atr_ratio        = _atr_ratio_ps,     # [v2.1] ATR 축소 이관
                is_bear_reversal = _is_bear_rev_ps,   # [v2.1] BR 축소 이관
            )

            # [v2.1] MDD-L2 + ATR 축소 → position_sizer.calculate() 내부로 이관
            # consec_loss / atr_ratio 파라미터로 전달됨 (위 calculate 호출 참조)

            # [v2.1] BEAR_REVERSAL 50% 축소 → position_sizer 내부로 이관

            # [FIX-RATIO] SURGE 신호는 confidence=score로 생성됨
            if _is_surge_kelly:
                _combined_score = getattr(signal, "confidence", 0.5)
            else:
                _ml_conf_score  = getattr(signal, "ml_confidence", 0.5)
                _ensemble_score = getattr(signal, "score",         0.5)
                _combined_score = (_ml_conf_score + _ensemble_score) / 2

            if _combined_score >= 0.80:
                _buy_ratio  = 1.0
                _buy_reason = f"강한신호({_combined_score:.2f}) 전량매수"
            elif _combined_score >= 0.60:
                _buy_ratio  = 0.70
                _buy_reason = f"중간신호({_combined_score:.2f}) 70%매수"
            else:
                _buy_ratio  = 0.50
                _buy_reason = f"약한신호({_combined_score:.2f}) 50%매수"

            # [EB-2] 시간대 배율: dir() 오용 수정 → _execute_buy 내 직접 계산
            from datetime import datetime as _dt_ts, timezone, timedelta as _td_ts
            _KST_TS = timezone(_td_ts(hours=9))
            _now_hour_exec = _dt_ts.now(_KST_TS).hour
            if 12 <= _now_hour_exec < 18:
                _time_size_mult_exec = 1.20
            elif 0 <= _now_hour_exec < 6:
                _time_size_mult_exec = 0.70
            else:
                _time_size_mult_exec = 1.00
            if _time_size_mult_exec != 1.0:
                _before_ts = position_size
                position_size *= _time_size_mult_exec
                logger.debug(
                    f'[TIME-SIZE] {market} {_now_hour_exec}시 '
                    f'배율={_time_size_mult_exec}× | '
                    f'₩{_before_ts:,.0f} → ₩{position_size:,.0f}'
                )
            # EB-4: position_sizer 0.0 반환 시 즉시 차단
            if position_size <= 0:
                logger.warning(
                    f"[EB-4] {market} position_sizer 반환 ₩0 → 주문 스킵"
                )
                self._buying_markets.discard(market)
                return
            _original_size     = position_size
            _min_entry_krw_eb3 = getattr(
                self.position_sizer, "MIN_ORDER_KRW", 5_000
            )
            _after_ratio = position_size * _buy_ratio

            # [v2.1] EB-3 구제 로직: buy_ratio 후 MIN_ORDER_KRW 미만이면
            # buy_ratio 포기하고 position_size 원본 사용 (자본 충분한 경우)
            if _after_ratio < _min_entry_krw_eb3:
                if position_size >= _min_entry_krw_eb3:
                    logger.info(
                        f"[EB-3-RESCUE] {market} buy_ratio 포기 | "
                        f"ratio후 ₩{_after_ratio:,.0f} → 원본 ₩{position_size:,.0f}"
                    )
                    _after_ratio = position_size
                else:
                    # position_size 자체가 MIN_ORDER_KRW 미만 → 정상 차단
                    logger.warning(
                        f"[EB-3] {market} ₩{_after_ratio:,.0f} "
                        f"< 최소 ₩{_min_entry_krw_eb3:,} → 주문 스킵"
                    )
                    self._buying_markets.discard(market)
                    return
            position_size = _after_ratio
            logger.info(
                f"   ({market}): {_buy_reason} | "
                f"₩{_original_size:,.0f} → ₩{position_size:,.0f}"
            )

            _MIN_POSITION_KRW = 20_000
            _MAX_POSITION_KRW = krw * 0.20

            if position_size < _MIN_POSITION_KRW:
                if krw >= _MIN_POSITION_KRW * 2:
                    position_size = _MIN_POSITION_KRW
                    logger.info(
                        f"    ({market}): "
                        f"₩{position_size:,.0f} (자본 ₩{krw:,.0f})"
                    )
                else:
                    logger.debug(
                        f"   ({market}): "
                        f"₩{position_size:,.0f} < 최소 ₩{_MIN_POSITION_KRW:,.0f}"
                    )
                    self._buying_markets.discard(market)
                    return

            if position_size > _MAX_POSITION_KRW:
                position_size = _MAX_POSITION_KRW
                logger.info(
                    f"    ({market}): "
                    f"₩{position_size:,.0f} (자본의 20%)"
                )

            if position_size < self.settings.trading.min_order_amount:
                logger.debug(
                    f"   ({market}): "
                    f"₩{position_size:,.0f} < "
                    f"최소 ₩{self.settings.trading.min_order_amount:,.0f}"
                )
                self._buying_markets.discard(market)
                return

            if self.portfolio.position_count >= self.settings.trading.max_positions:
                logger.info(
                    f"    ({market}): "
                    f"{self.portfolio.position_count}/"
                    f"{self.settings.trading.max_positions} → 매수 취소"
                )
                self._buying_markets.discard(market)
                return

            current_price    = self._market_prices.get(market, float(last["close"]))
            _buy_raw_volume  = position_size / current_price if current_price > 0 else 0
            _buy_volume      = _floor_vol(market, _buy_raw_volume)
            _adjusted_krw    = _buy_volume * current_price if _buy_volume > 0 else position_size

            req = ExecutionRequest(
                market=market,
                side=OrderSide.BUY,
                amount_krw=_adjusted_krw,
                reason=signal.reasons[0] if getattr(signal, 'reasons', []) else "BUY signal",
                strategy_name=", ".join(getattr(signal, 'contributing_strategies', [])),
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

            try:
                result = await self.executor.execute(req)
            finally:
                self._buying_markets.discard(market)

            if result.executed_price > 0:
                self.portfolio.open_position(
                    market=market,
                    entry_price=result.executed_price,
                    volume=result.executed_volume,
                    amount_krw=position_size,
                    strategy=req.strategy_name,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                )
                self._last_signal_time[market] = time.time()  # [FIX] 체결 후 갱신
                self.trailing_stop.add_position(
                    market, result.executed_price, stop_loss, atr
                )

                try:
                    if self.ppo_online_trainer is not None:
                        self.ppo_online_trainer.add_experience(
                            market=market, action=1,
                            profit_rate=0.0, hold_hours=0.0,
                        )
                except Exception as _ppo_buy_e:
                    logger.debug(f"PPO BUY   : {_ppo_buy_e}")

                if self.position_mgr_v2 is not None:
                    try:
                        from risk.position_manager_v2 import PositionV2
                        _pos_v2 = PositionV2(
                            market=market,
                            entry_price=result.executed_price,
                            volume=result.executed_volume,
                            amount_krw=position_size,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            strategy=req.strategy_name,
                        )
                        self.position_mgr_v2.add_position(_pos_v2)
                    except Exception as _pv2_e:
                        logger.debug(f"PositionManagerV2  : {_pv2_e}")

                self.partial_exit.add_position(
                    market=market,
                    entry_price=result.executed_price,
                    volume=result.executed_volume,
                    take_profit=take_profit,
                )

                _fee_rate = getattr(self.settings.trading, "fee_rate", 0.0005)
                _buy_fee  = position_size * _fee_rate

                log_trade(
                    "BUY", market, result.executed_price,
                    position_size, req.reason
                )
                # [CB-FIX] 매수 후 KRW 캐시 차감
                try:
                    self._cached_krw = max(
                        0.0,
                        getattr(self, "_cached_krw", 0.0) - float(position_size)
                    )
                    logger.debug(f"[CB] BUY 후 _cached_krw={self._cached_krw:,.0f}")
                except Exception:
                    pass
                await self.telegram.notify_buy(
                    market, result.executed_price, position_size,
                    req.reason, req.strategy_name
                )

                # [REFACTOR-W1] insert_trade / upsert_position 분리 (try 중첩 제거)
                try:
                    await self.db_manager.insert_trade({
                        "timestamp":   datetime.now().isoformat(),
                        "market":      market,
                        "side":        "BUY",
                        "price":       result.executed_price,
                        "volume":      result.executed_volume,
                        "amount_krw":  position_size,
                        "fee":         _buy_fee,
                        "profit_rate": 0.0,
                        "strategy":    req.strategy_name,
                        "reason":      req.reason,
                    })
                except Exception as _db_e:
                    logger.warning(f"[BUY-DB] {market} insert_trade 오류: {_db_e}")

                # [REFACTOR-W1] positions 테이블 저장 (독립 try)
                try:
                    import time as _t_ups
                    await self.db_manager.upsert_position({
                        "market":         market,
                        "entry_price":    result.executed_price,
                        "volume":         result.executed_volume,
                        "amount_krw":     position_size,
                        "stop_loss":      stop_loss,
                        "take_profit":    take_profit,
                        "strategy":       req.strategy_name,
                        "entry_time":     _t_ups.time(),
                        "pyramid_count":  0,
                        "partial_exited": False,
                        "breakeven_set":  False,
                        "max_price":      result.executed_price,
                    })
                    logger.debug(f"[UPSERT-POS] {market} positions 저장 완료")
                except Exception as _ups_e:
                    logger.warning(f"[UPSERT-POS] {market} 저장 오류: {_ups_e}")

                try:
                    await self.db_manager.log_signal({
                        "market":      market,
                        "signal_type": "BUY",
                        "score":       getattr(signal, "score",      0),
                        "confidence":  getattr(signal, "confidence", 0),
                        "strategies":  list(getattr(signal, "contributing_strategies", [])),
                        "regime":      getattr(signal, "regime",     ""),
                        "executed":    True,
                    })
                except Exception as _sl_e:
                    logger.debug(f"signal_log executed  : {_sl_e}")

            try:
                _exec_price = float(getattr(result, "executed_price",
                              getattr(result, "price", 0)))
                _exec_qty   = float(getattr(result, "executed_volume",
                              getattr(result, "quantity",
                              getattr(result, "qty", 0))))
                if _exec_qty > 0 and _exec_price > 0:
                    self._wallet.record_buy(_symbol, _exec_qty, _exec_price)
            except Exception as _we:
                logger.debug(f"SmartWallet record_buy : {_we}")

        # ── 부분 청산 실행 ───────────────────────────────────────────
        finally:
            self._buying_markets.discard(market)  # [DUP-GUARD] 선점 해제 (모든 경로)
