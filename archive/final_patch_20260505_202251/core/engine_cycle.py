"""
core/engine_cycle.py
─────────────────────────────────────────────────────────────
메인 사이클 및 포지션 관리 Mixin

포함 메서드:
    _check_circuit_breaker      : 서킷브레이커 확인
    _main_loop                  : 메인 루프
    _cycle                      : 1사이클 실행
    _check_time_based_exits     : 시간 기반 청산 확인
    _check_position_exits       : SL/TP 청산 확인
    _analyze_existing_position  : 보유 포지션 분석
    _apply_walk_forward_params  : 워크포워드 파라미터 적용
    _load_strategies            : 전략 로드
    _market_scanner             : 시장 스캐너
    _get_all_krw_markets        : 전체 KRW 마켓 조회
    _check_surge                : 급등 감지
    _get_active_markets         : 활성 마켓 조회
    _run_backtest_v2            : 백테스트 실행
    _run_backtest_all           : 전체 백테스트 실행
─────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import pathlib
import time
from datetime import datetime
from utils.helpers import now_kst, Timer
from core.state_machine import BotState
from core.market_regime import GlobalMarketRegimeDetector, GlobalRegime
import asyncio
from typing import Optional
from loguru import logger


class EngineCycleMixin:
    """메인 사이클, 포지션 관리, 시장 스캐너 관련 메서드 Mixin"""

    async def _check_circuit_breaker(self) -> bool:
        """. True=.

         FIX v2.0.1:    
                  hasattr   1  →"""
        try:
            import datetime as _dt
            daily_loss_limit = getattr(
                self.settings.risk, "daily_loss_limit", 0.03
            )

            if self.adapter.is_paper:
                krw = self.adapter._paper_balance.get("KRW", 0)
            else:
                krw = getattr(self, "_cached_krw", 0.0)  # [CB-FIX] LIVE: 캐시된 KRW 사용
            current = self.portfolio.get_total_value(krw)
            now     = _dt.datetime.now()

            # ✅ FIX: 최초 초기화
            if not hasattr(self, "_day_start_balance"):
                self._day_start_balance = current
                self._day_start_date    = now.date()
                return False

            # ✅ FIX: 자정이 지나면 기준값 리셋
            if now.date() != self._day_start_date:
                logger.info(
                    f"   |  : "
                    f"₩{self._day_start_balance:,.0f} → ₩{current:,.0f}"
                )
                self._day_start_balance = current
                self._day_start_date    = now.date()
                return False

            if self._day_start_balance <= 0:
                return False

            loss_pct = (
                (self._day_start_balance - current)
                / self._day_start_balance
            )

            if loss_pct >= daily_loss_limit:
                logger.warning(
                    f" Circuit Breaker ! "
                    f"  {loss_pct:.1%} "
                    f"( {daily_loss_limit:.1%}) "
                    f"— 신규 매수 차단"
                )
                return True

            return False

        except Exception as _e:
            logger.error(f"[circuit_breaker] {_e}")
            return False

    # ── 메인 루프 ────────────────────────────────────────────────

    async def _main_loop(self):
        while self.state_machine.state != BotState.STOPPED:
            try:
                if self.state_machine.state == BotState.RUNNING:
                    with Timer("메인 루프 사이클"):
                        _cb_result = await self._check_circuit_breaker()
                        self._cb_main_loop_active = _cb_result  # [U9b-PATCH] _cycle과 동기화
                        if _cb_result:
                            await asyncio.sleep(60)
                            continue
                        await self._cycle()
                elif self.state_machine.state == BotState.PAUSED:
                    logger.debug("⏸  ...")
                    await asyncio.sleep(10)
                    continue
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f" : {e}")
                await asyncio.sleep(30)


    async def _cycle(self):
        # [LiveGuard] 사이클 시작 시 긴급 중단 파일 즉시 확인
        # [LiveGuard] EMERGENCY_STOP 디버그 체크
        _es_abs = pathlib.Path(__file__).parent.parent / "EMERGENCY_STOP"
        _es_rel = pathlib.Path("EMERGENCY_STOP")
        _es_cwd = pathlib.Path.cwd() / "EMERGENCY_STOP"
        logger.debug(f"[ES_CHECK] abs={_es_abs} exists={_es_abs.exists()} | cwd={pathlib.Path.cwd()}")
        if _es_abs.exists() or _es_rel.exists() or _es_cwd.exists():
            logger.warning("[LiveGuard] 🚨 EMERGENCY_STOP 감지 — 이번 사이클 전체 스킵")
            return
        # ── ML 예측용 변수 초기화 (NameError 방지) ──────────────────────
        _ml_df     = None
        _ml_market = "KRW-BTC"
        try:
            _active_markets = list(getattr(self, "_surge_cache", {}).keys())
            if not _active_markets:
                _active_markets = list(getattr(
                    self.portfolio, "open_positions", {}
                ).keys())
            if _active_markets:
                _ml_market = _active_markets[0]
        except Exception:
            pass
        try:
            _ml_df = self.cache_manager.get_ohlcv(_ml_market, "1h")
        except Exception:
            pass
        # ── ML 초기화 끝 ────────────────────────────────────────────────

        # [MDD-L3] 포트폴리오 서킷브레이커
        try:
            from datetime import datetime as _dt
            _today          = _dt.now().strftime("%Y-%m-%d")
            _daily_loss_key = f"_daily_loss_{_today}"
            _daily_loss     = getattr(self, _daily_loss_key, 0.0)
            _krw_bal        = getattr(self, "_krw_balance", 0)
            _dl_limit_cfg   = getattr(getattr(self, 'settings', None), 'risk', None)
            _dl_pct         = getattr(_dl_limit_cfg, 'daily_loss_limit', 0.03)
            _loss_limit     = _krw_bal * _dl_pct
            if _daily_loss < -_loss_limit and _loss_limit > 0:
                logger.warning(
                    f"[MDD-L3] 🚨 서킷브레이커 발동! "
                    f"일일손실 ₩{abs(_daily_loss):,.0f} > "
                    f"한도 ₩{_loss_limit:,.0f} ({_dl_pct*100:.0f}%) → 신규매수 중단"
                )
                self._circuit_breaker_active = True
            else:
                # [U9-PATCH] _main_loop CB와 동기화: _cb_main_loop_active 참조
                self._circuit_breaker_active = getattr(self, "_cb_main_loop_active", False)
            # [LiveGuard-C] 오늘 손실률 → live_guard 동기화
            if hasattr(self, "live_guard") and self.live_guard is not None:
                try:
                    _krw_now = getattr(self, "_krw_balance", 1) or 1
                    self.live_guard._today_loss_pct = _daily_loss / _krw_now
                except Exception:
                    pass
        except Exception as _e:
            logger.debug(f"[MDD-L3] 서킷브레이커 체크 오류: {_e}")

        # [C-1 FIX] ML 대시보드 업데이트는 engine_ml.py의
        # _get_ml_prediction()이 호출될 때마다 자동 처리됨
        # → 이 위치의 중복 dead code 제거 완료

        # ── APEX 핵심 사이클 (자동 삽입 fix_cycle_core) ─────────────

        # 1) 기존 포지션 청산 체크 (SL/TP/트레일링/M4)
        try:
            await self._check_position_exits()
        except Exception as _ce:
            logger.warning(f"[cycle] _check_position_exits 오류: {_ce}", exc_info=True)

        # 2) 시간 기반 강제청산 (72h / 48h / 24h)
        try:
            await self._check_time_based_exits()
        except Exception as _ce:
            logger.warning(f"[cycle] _check_time_based_exits 오류: {_ce}", exc_info=True)

        # 3) 기존 포지션 재평가 (ML 신호 기반 익절/손절)
        try:
            for _om in list(self.portfolio.open_positions.keys()):
                await self._analyze_existing_position(_om)
        except Exception as _ce:
            logger.warning(f"[cycle] _analyze_existing_position 오류: {_ce}", exc_info=True)

        # ── [PENDING-QUEUE] 대기열 처리 + 교체매매 ────────────────
        try:
            import time as _pq_t
            _TTL_SEC   = 1800  # 기본 30분
                    # scr 점수 비례 동적 TTL 적용 (대기열 추가 시점 기준)
            _REPLACE_SCORE = 0.80   # 교체매매 최소 surge score
            _REPLACE_PNL   = -1.5   # 교체매매 대상 최소 손실 (%)
            _REPLACE_HOLD  = 30     # 교체매매 최소 보유시간 (분)
            if hasattr(self, '_pending_surge_queue') and self._pending_surge_queue:
                _now_t = _pq_t.time()
                # TTL 만료 항목 제거
                _valid = [(m, s, t) for m, s, t in self._pending_surge_queue
                          if _now_t - t < _TTL_SEC]
                from collections import deque as _dq2
                self._pending_surge_queue = _dq2(_valid, maxlen=5)
                _open_pos  = self.portfolio.open_positions
                _max_pos   = getattr(self.settings.trading, 'max_positions', 10)
                _slot_free = len(_open_pos) < _max_pos
                if self._pending_surge_queue:
                    _pq_market, _pq_score, _pq_t2 = self._pending_surge_queue[0]
                    _age_min = (_now_t - _pq_t2) / 60
                    if _slot_free:
                        # 슬롯 생겼으면 즉시 매수
                        self._pending_surge_queue.popleft()
                        logger.info(f'[PENDING-QUEUE] {_pq_market} 슬롯 확보 → 즉시 매수 시도 (대기{_age_min:.1f}분)')
                        try:
                            import asyncio as _pq_aio
                            await _pq_aio.wait_for(self._analyze_market(_pq_market), timeout=8.0)
                        except Exception as _pq_e:
                            logger.warning(f'[PENDING-QUEUE] {_pq_market} 매수 실패: {_pq_e}', exc_info=True)
                    elif _pq_score >= _REPLACE_SCORE:
                        # 슬롯 없음 + 고score → 교체매매 검토
                        _worst_m   = None
                        _worst_pnl = 0.0
                        _worst_hold = 0.0
                        for _pm, _pp in _open_pos.items():
                            _ep = getattr(_pp, 'entry_price', 0)
                            _cp = self._market_prices.get(_pm, 0)
                            if _ep > 0 and _cp > 0:
                                _ppnl = (_cp - _ep) / _ep * 100
                                _et   = getattr(_pp, 'entry_time', _pq_t.time())
                                if isinstance(_et, (int, float)):
                                    _phold = (_now_t - _et) / 60
                                else:
                                    _phold = 0.0
                                if (_ppnl <= _REPLACE_PNL
                                        and _phold >= _REPLACE_HOLD
                                        and _ppnl < _worst_pnl):
                                    _worst_m   = _pm
                                    _worst_pnl = _ppnl
                                    _worst_hold = _phold
                        if _worst_m:
                            logger.info(
                                f'[REPLACE] {_worst_m}(pnl={_worst_pnl:.2f}%, {_worst_hold:.0f}분) → '
                                f'{_pq_market}(score={_pq_score:.3f}) 교체매매 시작'
                            )
                            try:
                                await self._execute_sell(_worst_m, '교체매매_surge진입')
                                self._pending_surge_queue.popleft()
                                import asyncio as _rp_aio
                                await _rp_aio.wait_for(self._analyze_market(_pq_market), timeout=8.0)
                            except Exception as _rp_e:
                                logger.warning(f'[REPLACE] 교체매매 실패: {_rp_e}', exc_info=True)
        except Exception as _pq_err:
            logger.warning(f'[PENDING-QUEUE] 처리 오류: {_pq_err}', exc_info=True)
        # ── 대기열 처리 끝 ──────────────────────────────────────────

        # 4) 신규 매수 스캔 (서킷브레이커 비활성 시만, 5개씩 배치)
        if not getattr(self, "_circuit_breaker_active", False):
            try:
                import asyncio as _aio
                _active = await self._get_active_markets()
                _open_now = set(self.portfolio.open_positions.keys())
                _buying_now = getattr(self, "_buying_markets", set())
                _targets = [m for m in _active
                            if m not in _open_now and m not in _buying_now]
                # [OPT] 최대 15개 제한 + 종목당 8초 타임아웃
                # [SURGE-OPT] 등락율 높은 종목 우선 → 최대 15개
                _change_rates = getattr(self, "_market_change_rates", {})
                _targets = sorted(_targets,
                                  key=lambda m: _change_rates.get(m, 0.0),
                                  reverse=True)[:15]
                # SurgeCache 감지 코인은 분석 대기열 최우선 삽입
                _surge_priority = [
                    m for m in getattr(self, "_surge_cache", {}).keys()
                    if m not in _open_now and m not in _buying_now
                ]
                # [PHASE2-B] 레짐별 우선 스캔 코인 실제 삽입
                _gr_cycle_p2 = str(getattr(
                    getattr(self, '_global_regime', None), 'value',
                    getattr(self, '_global_regime', 'UNKNOWN')
                )).upper()
                _REGIME_PRIORITY_P2 = {
                    'BULL':       ['KRW-BTC','KRW-ETH','KRW-SOL','KRW-XRP','KRW-DOGE','KRW-ADA'],
                    'RECOVERY':   ['KRW-BTC','KRW-ETH','KRW-ADA','KRW-AVAX','KRW-DOT','KRW-LINK'],
                    'BEAR_WATCH': ['KRW-BTC','KRW-ETH','KRW-XRP','KRW-ADA','KRW-LINK','KRW-DOT'],
                    'BEAR':       ['KRW-BTC','KRW-ETH'],
                }
                _regime_priority_coins = [
                    m for m in _REGIME_PRIORITY_P2.get(_gr_cycle_p2, [])
                    if m not in _open_now and m not in _buying_now
                ]
                # 우선순위: SURGE캐시 > 레짐우선코인 > 등락률순
                _combined = _surge_priority.copy()
                for _rc in _regime_priority_coins:
                    if _rc not in _combined:
                        _combined.append(_rc)
                for _tc in _targets:
                    if _tc not in _combined:
                        _combined.append(_tc)
                _targets = _combined[:15]
                if _surge_priority:
                    logger.info(f"[SURGE-INJECT] SurgeCache→targets 삽입: {_surge_priority}")
                if _targets:
                    _top3 = [(m, round(_change_rates.get(m, 0) * 100, 2)) for m in _targets[:3]]
                    logger.debug(f"[SURGE-SCAN] 우선순위 상위3: {_top3}")
                _batch_size = 5
                for _bi in range(0, len(_targets), _batch_size):
                    _batch = _targets[_bi:_bi + _batch_size]
                    async def _safe_scan(m):
                        try:
                            await _aio.wait_for(
                                self._analyze_market(m), timeout=8.0
                            )
                        except _aio.TimeoutError:
                            logger.debug(f"[CYCLE] {m} 타임아웃(8s) 스킵")
                        except Exception as _se:
                            logger.debug(f"[CYCLE] {m} 스캔오류: {_se}")
                    await _aio.gather(
                        *[_safe_scan(m) for m in _batch],
                        return_exceptions=True
                    )
                    await _aio.sleep(0.1)  # 배치 간 CPU 양보
            except Exception as _ce:
                logger.warning(f"[cycle] buy_scan 오류: {_ce}", exc_info=True)

        # 5) 동적 마켓 스캐너 (급등 코인 감지) — 최대 60초
        # [BUG-SC-3 FIX] 주석 수정: 20초 → 60초 (25코인×REST 호출 기준)
        try:
            import asyncio as _aio2
            await _aio2.wait_for(self._market_scanner(), timeout=60.0)
        except _aio2.TimeoutError:
            logger.debug("[cycle] _market_scanner 타임아웃(20s) 스킵")
        except Exception as _ce:
            logger.warning(f"[cycle] _market_scanner 오류: {_ce}", exc_info=True)
        # ── 핵심 사이클 끝 ──────────────────────────────────────────

    # ── 시간기반 강제청산 ────────────────────────────────────────

    def _get_reliable_price(self, market: str) -> float:
        """[FIX-RELIABLE-PRICE] WS -> REST 순서로 신뢰할 수 있는 현재가 반환
        Returns 0.0 if both sources fail.
        """
        # 1순위: WebSocket 실시간 가격
        price = self._market_prices.get(market, 0)
        if price and price > 0:
            return float(price)
        # 2순위: pyupbit REST API fallback
        try:
            import pyupbit
            _rest = pyupbit.get_current_price(market)
            if _rest and float(_rest) > 0:
                _p = float(_rest)
                self._market_prices[market] = _p  # 캐시 업데이트
                logger.debug(f'[RELIABLE-PRICE] {market} REST fallback: {_p:,.2f}')
                return _p
        except Exception as _rp_e:
            logger.debug(f'[RELIABLE-PRICE] {market} REST 실패: {_rp_e}')
        return 0.0

    async def _check_time_based_exits(self) -> None:
        now     = datetime.now()
        markets = list(self.portfolio.open_positions.keys())

        for market in markets:
            try:
                pos = self.portfolio.get_position(market)
                if not pos:
                    continue
                current_price = self._get_reliable_price(market)
                if not current_price or current_price <= 0:
                    logger.debug(f'[TIME-EXIT-SKIP] {market} 가격 미수신 → 스킵')
                    continue
                entry_time = (
                    getattr(pos, "entry_time",  None)
                    or getattr(pos, "created_at", None)
                )
                if entry_time is None:
                    # ── BUG-4 FIX: entry_time=None 즉시 강제청산 방지 ──────
                    # 봇 재시작 직후 DB 복원 포지션은 entry_time이 None일 수 있음
                    # → 봇 시작 후 1시간 유예, 이후에만 강제청산
                    _bot_start = getattr(self, "_bot_start_time", None)
                    if _bot_start is None:
                        logger.warning(
                            f"[TIME-EXIT] {market}: entry_time=None "
                            f"→ _bot_start_time 미설정, 이번 사이클 스킵"
                        )
                        continue
                    _elapsed_h = (now - _bot_start).total_seconds() / 3600
                    if _elapsed_h < 1.0:
                        logger.info(
                            f"[TIME-EXIT] {market}: entry_time=None "
                            f"봇시작 {_elapsed_h:.1f}h → 1h 유예 중"
                        )
                        continue
                    logger.info(
                        f"[TIME-EXIT] {market}: entry_time=None "
                        f"봇시작 {_elapsed_h:.1f}h 경과 → 강제청산"
                    )
                    await self._execute_sell(market, 'entry_time_없음_강제청산', current_price)
                    continue
                    # ── BUG-4 FIX 끝 ────────────────────────────────────────
                if isinstance(entry_time, str):
                    try:
                        entry_time = datetime.fromisoformat(entry_time)
                    except Exception:
                        continue

                elif isinstance(entry_time, float):
                    entry_time = datetime.fromtimestamp(entry_time)
                held_hours  = (now - entry_time).total_seconds() / 3600
                profit_rate = (current_price - pos.entry_price) / pos.entry_price

                # [PHASE1-SURGE-HOLD] SURGE 횡보 조기 청산
                _pos_strat_p1 = getattr(pos, "strategy", "") or ""
                if "SURGE" in _pos_strat_p1:
                    # Case A: 1시간 경과 + ±0.5% 횡보 → 급등 실패
                    if held_hours >= 1.0 and profit_rate < 0.005 and abs(profit_rate) < 0.015:  # 상승 중 제외, 손실/횡보만 청산
                        logger.info(
                            f"[SURGE-SIDEWAYS] {market} | "
                            f"보유={held_hours:.1f}h | "
                            f"수익={profit_rate*100:.2f}% | "
                            f"급등실패 횡보청산"
                        )
                        await self._execute_sell(
                            market, "SURGE_횡보탈출_1h", current_price
                        )
                        continue
                    # Case B: 4시간 경과 + 손실 중 → 강제청산
                    if held_hours >= 4.0 and profit_rate < 0.005:   # 수수료 감안 +0.5% 미만 4h 후 청산
                        logger.info(
                            f"[SURGE-MAXHOLD] {market} | "
                            f"보유={held_hours:.1f}h | "
                            f"수익={profit_rate*100:.2f}% | "
                            f"4h 손실 강제청산"
                        )
                        await self._execute_sell(
                            market, "SURGE_손실청산_4h", current_price
                        )
                        continue

                if held_hours >= 72:
                    logger.info(
                        f" 72h  ({market}): "
                        f"보유={held_hours:.1f}h | 수익={profit_rate*100:.2f}%"
                    )
                    await self._execute_sell(market, "시간초과_72h_강제청산", current_price)
                    continue

                if held_hours >= 48 and -0.03 <= profit_rate <= 0.03:
                    logger.info(
                        f" 48h  ({market}): "
                        f"보유={held_hours:.1f}h | 수익={profit_rate*100:.2f}%"
                    )
                    await self._execute_sell(market, "횡보청산_48h", current_price)
                    continue

                # 전략별 24h 손실 기준 분리 (SURGE vs 일반)
                _loss_thr_24h = -0.015 if "SURGE" in (_pos_strat_p1 or "") else -0.020
                if held_hours >= 24 and profit_rate <= _loss_thr_24h:
                    logger.info(
                        f" 24h  ({market}): "
                        f"보유={held_hours:.1f}h | 수익={profit_rate*100:.2f}% "
                        f"(기준={_loss_thr_24h*100:.1f}%)"
                    )
                    await self._execute_sell(market, "손실청산_24h", current_price)
                    continue

            except Exception as _te:
                logger.debug(f"   ({market}): {_te}")

    # ── 포지션 청산 체크 ─────────────────────────────────────────

    async def _check_position_exits(self):
        markets = list(self.portfolio.open_positions.keys())
        for market in markets:
            try:
                current_price = self._get_reliable_price(market)
                if not current_price or current_price <= 0:
                    logger.debug(f'[ANALYZE-SKIP] {market} 가격 미수신 → 스킵')
                    continue
                pos = self.portfolio.get_position(market)
                if pos is None:
                    continue

                entry_price = (
                    getattr(pos, "avg_price",   None)
                    or getattr(pos, "entry_price", None)
                    or (pos.get("avg_price") if isinstance(pos, dict) else None)
                    or 0
                )
                if entry_price <= 0:
                    continue

                try:
                    _df_pos = self.cache_manager.get_ohlcv(market)
                    if _df_pos is not None and len(_df_pos) >= 20:
                        _profit_pct = (current_price - entry_price) / entry_price
                        # 전략별 SL cap 분기: SURGE=1.3%, 일반=1.7%
                        _pos_strat = getattr(self.portfolio.get_position(market), "strategy", "")
                        _is_surge_a = "SURGE" in (_pos_strat or "")
                        _sl_cap_val = 0.987 if _is_surge_a else 0.983
                        _sl_levels  = self.atr_stop.get_dynamic_levels(
                            _df_pos, entry_price, current_price, _profit_pct,
                            market=market,
                            is_surge=_is_surge_a,
                            global_regime=getattr(self, "_global_regime", None),
                        )
                        basic_sl = max(_sl_levels.stop_loss, entry_price * _sl_cap_val)
                        basic_tp = _sl_levels.take_profit
                        if _profit_pct >= 0.03:
                            logger.info(
                                f" SL   ({market}): "
                                f"SL={basic_sl:,.0f} | "
                                f"수익={_profit_pct*100:.2f}% | "
                                f"RR={_sl_levels.rr_ratio:.2f}"
                            )
                    else:
                        basic_sl = entry_price * (
                            1 - getattr(self.settings.risk, "stop_loss_pct", 0.015)
                        )
                        basic_tp = entry_price * (
                            1 + getattr(self.settings.risk, "take_profit_pct", 0.05)
                        )
                except Exception as _dyn_e:
                    logger.debug(f"ATR    ({market}): {_dyn_e}")
                    basic_sl = entry_price * (
                        1 - getattr(self.settings.risk, "stop_loss_pct", 0.015)
                    )
                    basic_tp = entry_price * (
                        1 + getattr(self.settings.risk, "take_profit_pct", 0.05)
                    )

                if current_price <= basic_sl:
                    # [MDD-L2] 연속손실 카운터 증가
                    _cl = getattr(self, "_consecutive_loss_count", 0)
                    self._consecutive_loss_count = _cl + 1
                    logger.debug(
                        f"[MDD-L2] 연속손실 카운터: {self._consecutive_loss_count}건"
                    )
                    loss_pct = (current_price - entry_price) / entry_price * 100
                    logger.info(
                        f"    ({market}): "
                        f"현재={current_price:,.0f} ≤ SL={basic_sl:,.0f} "
                        f"({loss_pct:.2f}%)"
                    )
                    await self._execute_sell(
                        market, f"기본손절_{loss_pct:.1f}%", current_price
                    )
                    continue

                if current_price >= basic_tp:
                    # [MDD-L2] 수익 청산 시 연속손실 카운터 리셋
                    self._consecutive_loss_count = 0
                    profit_pct = (current_price - entry_price) / entry_price * 100
                    logger.info(
                        f"    ({market}): "
                        f"현재={current_price:,.0f} ≥ TP={basic_tp:,.0f} "
                        f"({profit_pct:.2f}%)"
                    )
                    await self._execute_sell(
                        market, f"기본익절_{profit_pct:.1f}%", current_price
                    )
                    continue

                exit_reason = self.trailing_stop.update(market, current_price)
                if exit_reason:
                    await self._execute_sell(market, exit_reason, current_price)
                    continue

                exit_volume   = self.partial_exit.check(market, current_price)
                _partial_done = False
                if exit_volume > 0:
                    await self._execute_partial_sell(market, exit_volume, current_price)
                    _partial_done = True

                if self.position_mgr_v2 is not None:
                    try:
                        _exit_sig = self.position_mgr_v2.check_exit(market, current_price)
                        if _exit_sig.should_exit:
                            logger.info(
                                f" M4   ({market}): "
                                f"사유={_exit_sig.reason.value} | "
                                f"비율={_exit_sig.volume_pct:.0%} | "
                                f"{_exit_sig.message}"
                            )
                            if _exit_sig.reason.value == "PARTIAL_EXIT":
                                if _partial_done:
                                    logger.debug(
                                        f"⏭ M4   ({market}): "
                                        f"PartialExit 이미 실행됨"
                                    )
                                else:
                                    _pos_v = self.portfolio.open_positions.get(market)
                                    if _pos_v:
                                        _sell_vol = (
                                            getattr(_pos_v, "volume", 0)
                                            * _exit_sig.volume_pct
                                        )
                                        if _sell_vol > 0:
                                            await self._execute_partial_sell(
                                                market, _sell_vol, current_price
                                            )
                            else:
                                await self._execute_sell(
                                    market,
                                    f"M4_{_exit_sig.reason.value}",
                                    current_price,
                                )
                    except Exception as _m4_e:
                        logger.debug(f"M4    ({market}): {_m4_e}")

            except Exception as _e:
                logger.debug(f"    ({market}): {_e}")

    # ── 기존 포지션 재평가 ───────────────────────────────────────

    async def _analyze_existing_position(self, market: str) -> None:
        try:
            pos = self.portfolio.get_position(market)
            if pos is None:
                return

            candles = self.cache_manager.get_ohlcv(market, "1h")
            if candles is None or (hasattr(candles, "__len__") and len(candles) < 20):
                try:
                    candles = await self.rest_collector.get_ohlcv(
                        market, interval="minute60", count=100
                    )
                except Exception:
                    candles = None

            try:
                _candle_len = len(candles) if candles is not None else 0
            except Exception:
                _candle_len = 0
            if _candle_len < 20:
                return

            ml_result = await self._get_ml_prediction(market, candles)
            if ml_result is None:
                return

            signal     = ml_result.get("signal",     "HOLD")
            confidence = ml_result.get("confidence", 0.0)

            if hasattr(pos, "avg_price"):
                entry_price = getattr(pos, "avg_price", 0) or getattr(pos, "entry_price", 0)
            elif hasattr(pos, "entry_price"):
                entry_price = getattr(pos, "entry_price", 0)
            elif isinstance(pos, dict):
                entry_price = pos.get("avg_price", pos.get("entry_price", 0))
            else:
                entry_price = 0

            current_price = self._get_reliable_price(market)
            # WS 미수신 시 REST fallback으로 현재가 조회
            if not current_price or current_price <= 0:
                try:
                    import asyncio as _aio_fb
                    _fb_ticker = await _aio_fb.wait_for(
                        self.rest_collector.get_ticker(market), timeout=2.0
                    ) if hasattr(self, 'rest_collector') else None
                    if _fb_ticker:
                        current_price = float(_fb_ticker.get('trade_price', 0))
                        if current_price > 0:
                            self._market_prices[market] = current_price  # 캐시 업데이트
                            logger.debug(f'[REST-FALLBACK] {market} 현재가 REST 조회: {current_price}')
                except Exception as _fb_e:
                    logger.debug(f'[REST-FALLBACK] {market} 실패: {_fb_e}')
            if not current_price or current_price <= 0:
                logger.debug(f"[ANALYZE] {market} _market_prices 미수신 → 스킵")
                return
            pnl_pct = (
                (current_price - entry_price) / entry_price * 100
                if entry_price > 0 else 0.0
            )
            # 비정상 pnl_pct 방어 — 재시작 직후 가격 미수신 시 스킵
            if pnl_pct <= -99.0:
                logger.warning(
                    f'[PNL-GUARD] {market} pnl_pct={pnl_pct:.1f}%'
                    ' → entry/current 가격 이상, 재평가 스킵'
                )
                return


            logger.debug(
                f"   | {market} | "
                f"ML={signal}({confidence:.2f}) | PnL={pnl_pct:+.2f}%"
            )

            if entry_price > 0 and current_price > 0 and _candle_len >= 20:
                try:
                    _profit_pct = (current_price - entry_price) / entry_price
                    _pos_strat_b = getattr(self.portfolio.get_position(market), 'strategy', '')
                    _is_surge_b = 'SURGE' in (_pos_strat_b or '')
                    _sl_cap_b = 0.987 if _is_surge_b else 0.983
                    _atr_levels = self.atr_stop.get_dynamic_levels(
                        candles, entry_price, current_price, _profit_pct,
                        is_surge=_is_surge_b
                    )
                    _basic_sl = max(_atr_levels.stop_loss, entry_price * _sl_cap_b)
                    _basic_tp = _atr_levels.take_profit

                    if _profit_pct >= 0.03:
                        logger.info(
                            f" SL   ({market}): "
                            f"SL={_basic_sl:,.0f} | "
                            f"수익={_profit_pct*100:.2f}% | "
                            f"RR={_atr_levels.rr_ratio:.2f}"
                        )

                    if current_price <= _basic_sl:
                        _loss_pct = _profit_pct * 100
                        logger.info(
                            f" ATR   ({market}): "
                            f"현재={current_price:,.0f} ≤ SL={_basic_sl:,.0f} "
                            f"({_loss_pct:.2f}%)"
                        )
                        await self._execute_sell(
                            market, f"기본손절_{_loss_pct:.1f}%", current_price
                        )
                        return

                    if current_price >= _basic_tp:
                        _profit_pct2 = _profit_pct * 100
                        logger.info(
                            f" ATR   ({market}): "
                            f"현재={current_price:,.0f} ≥ TP={_basic_tp:,.0f} "
                            f"({_profit_pct2:.2f}%)"
                        )
                        await self._execute_sell(
                            market, f"ATR익절_{_profit_pct2:.1f}%", current_price
                        )
                        return

                except Exception as _atr_e:
                    logger.debug(f"ATR     ({market}): {_atr_e}")


            # 최소 보유 30분 - 매수 직후 손절 방지
            _pos_et = getattr(pos, "entry_time", None) or getattr(pos, "created_at", None)
            _held_min = 0
            import datetime as _dt_hold  # _dt_hold import
            if _pos_et:
                try:
                    if isinstance(_pos_et, str):
                        _et = _dt_hold.datetime.fromisoformat(_pos_et)
                    elif isinstance(_pos_et, (int, float)):
                        _et = _dt_hold.datetime.fromtimestamp(_pos_et)  # float Unix timestamp 처리
                    else:
                        _et = _pos_et
                    _held_min = (_dt_hold.datetime.now() - _et).total_seconds() / 60
                except Exception:
                    _held_min = 999
            # [ML-VETO v2] 최소보유 30분 복원 + ML SELL 차단 강화
            if _held_min < 30 and pnl_pct > -2.5:
                logger.debug(
                    f"  ({market}): 최소보유 미달 {_held_min:.1f}min < 30min, SELL 차단"
                )
            elif (
                # ML 익절: 30분 이상 보유 + SELL 신호 + 최소 +0.5% 수익
                (signal == "SELL" and confidence >= 0.65
                    and pnl_pct >= 0.5 and _held_min >= 30) or
                # 강한 수익 익절: 30분 이상 + 1.5% 이상
                (confidence >= 0.65 and pnl_pct >= 1.5 and _held_min >= 30) or
                # ML 손절: 60분 이상 보유 후에만 허용 (매수 직후 차단)
                (confidence >= 0.65 and pnl_pct <= -1.5 and _held_min >= 60) or
                # 무조건 익절: 3% 이상
                (pnl_pct >= 3.0) or
                # 장기 보유 손절: 12h+ 이후 -2% 이상
                (pnl_pct <= -2.0 and (confidence >= 0.50 or _held_min >= 720)) or
                # 시간 기반 익절
                (pnl_pct >= self._time_based_tp_threshold(market))
            ):
                _ml_reason = f"ML익절_{pnl_pct:.1f}%" if pnl_pct >= 0 else f"ML손절_{pnl_pct:.1f}%"
                logger.info(
                    f" ML 청산 | {market} | confidence={confidence:.2f} | pnl={pnl_pct:+.2f}% | reason={_ml_reason}"
                )
                await self._execute_sell(
                    market, _ml_reason, current_price
                )
                return

        except Exception as e:
            import traceback
            logger.debug(
                f"   ({market}): {e} | "
                f"{traceback.format_exc().splitlines()[-1]}"
            )

    # ── 신규 마켓 분석 ───────────────────────────────────────────

    def _apply_walk_forward_params(self):
        try:
            from backtesting.walk_forward import WalkForwardRunner
            params = WalkForwardRunner.load_optimized_params()
            if not params:
                logger.info("Walk-Forward 파라미터 없음 → 기본값 유지")
                return
            # load_optimized_params가 전체 dict를 반환할 경우 strategies 키 파싱
            if "strategies" in params:
                params = params["strategies"]
            applied = 0
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
                    )
                else:
                    if info.get("params"):
                        strategy.params.update(info["params"])
                    weight_boost = info.get("weight_boost", 1.0)
                    if weight_boost != 1.0:
                        old_weight = self.signal_combiner.STRATEGY_WEIGHTS.get(
                            strategy_name, 1.0
                        )
                        new_weight = old_weight * weight_boost
                        self.signal_combiner.STRATEGY_WEIGHTS[strategy_name] = new_weight
                        logger.info(
                            f"   {strategy_name}  "
                            f"{old_weight:.1f} → {new_weight:.1f} "
                            f"(boost={weight_boost}x)"
                        )
                    applied += 1
            logger.success(f"✅ Walk-Forward 파라미터 적용: {applied}개 전략")
        except Exception as e:
            logger.warning(f"Walk-Forward    ( ): {e}")


    def _load_strategies(self):
        from strategies.momentum.macd_cross import MACDCrossStrategy
        from strategies.momentum.rsi_divergence import RSIDivergenceStrategy
        from strategies.momentum.supertrend import SupertrendStrategy
        from strategies.mean_reversion.bollinger_squeeze import BollingerSqueezeStrategy
        # [ST-1] from strategies.mean_reversion.vwap_reversion import VWAPReversionStrategy  # 비활성화: -₩3,158
        # VolBreakout 전략 비활성화 — 백테스트 승률 29%, 기대값 -0.270%
        # from strategies.volatility.vol_breakout import VolBreakoutStrategy
        from strategies.volatility.atr_channel import ATRChannelStrategy
        from strategies.v2.order_block_v2 import OrderBlockStrategy2 as OrderBlockStrategy  # [REFACTOR] v2 활성

        strategies = [
            MACDCrossStrategy(), RSIDivergenceStrategy(), SupertrendStrategy(),
            BollingerSqueezeStrategy(),
            ATRChannelStrategy(), OrderBlockStrategy(),
            # [ST-1] VWAPReversionStrategy() 제거: -₩3,158, 승률 42% (2026-05-03)
            # [ST-2] VolBreakoutStrategy() 제거: -₩3,521, 승률 29% (이전 패치)
        ]
        for s in strategies:
            self._strategies[s.NAME] = s
        logger.info(f" {len(self._strategies)}개 전략 로드 완료")


    async def _market_scanner(self) -> list:
        cfg = self._SCANNER_CONFIG
        now = time.time()
        if now - self._last_scan_time < cfg["interval_sec"]:
            return []
        self._last_scan_time = now

        try:
            # GlobalRegime 5분 주기 갱신 (_market_scanner 매 진입 시 체크)
            try:
                _btc_df_gr = self.cache_manager.get_ohlcv("KRW-BTC")
                if _btc_df_gr is not None and len(_btc_df_gr) >= 50:
                    _new_regime = self.global_regime_detector.detect(_btc_df_gr)
                    # [PHASE2-A] 레짐 변경 시에만 가중치/쿼터 갱신 (race condition 방지)
                    if not hasattr(self, '_global_regime') or self._global_regime != _new_regime:
                        self._global_regime = _new_regime
                        _gr_str_p2 = str(getattr(_new_regime, 'value', _new_regime)).upper()
                        # 전략 가중치 테이블
                        # [BUG-SC-2 FIX] SURGE_FASTENTRY 레짐별 가중치/쿼터 재조정
                        # 근거: SURGE_FASTENTRY 327건 승률46% EV-0.126% → MDD 76% 원인
                        _REGIME_WEIGHTS_P2 = {
                            'BULL':       {'SURGE_FASTENTRY': 1.2, 'OrderBlock_SMC': 1.2, 'MACD_Cross': 1.0, 'Bollinger_Squeeze': 0.9, 'ML_Ensemble': 1.0},
                            'RECOVERY':   {'SURGE_FASTENTRY': 0.5, 'OrderBlock_SMC': 1.3, 'MACD_Cross': 1.1, 'Bollinger_Squeeze': 1.2, 'ML_Ensemble': 1.1},
                            'BEAR_WATCH': {'SURGE_FASTENTRY': 0.0, 'OrderBlock_SMC': 1.5, 'MACD_Cross': 0.9, 'Bollinger_Squeeze': 1.8, 'ML_Ensemble': 1.3},
                            'BEAR':       {'SURGE_FASTENTRY': 0.0, 'OrderBlock_SMC': 1.2, 'MACD_Cross': 0.8, 'Bollinger_Squeeze': 1.5, 'ML_Ensemble': 1.0},
                        }
                        # 슬롯 쿼터 테이블 (합계 > max_positions → 전략 간 자연 경쟁 유도)
                        _QUOTA_MAP_P2 = {
                            'BULL':       {'SURGE_FASTENTRY': 4, 'OrderBlock_SMC': 4, 'MACD_Cross': 3, 'Bollinger_Squeeze': 3, 'ML_Ensemble': 2},
                            'RECOVERY':   {'SURGE_FASTENTRY': 1, 'OrderBlock_SMC': 4, 'MACD_Cross': 3, 'Bollinger_Squeeze': 4, 'ML_Ensemble': 3},
                            'BEAR_WATCH': {'SURGE_FASTENTRY': 0, 'OrderBlock_SMC': 5, 'MACD_Cross': 2, 'Bollinger_Squeeze': 5, 'ML_Ensemble': 3},
                            'BEAR':       {'SURGE_FASTENTRY': 0, 'OrderBlock_SMC': 6, 'MACD_Cross': 2, 'Bollinger_Squeeze': 5, 'ML_Ensemble': 3},
                        }
                        _w = _REGIME_WEIGHTS_P2.get(_gr_str_p2, _REGIME_WEIGHTS_P2['BEAR_WATCH'])
                        _q = _QUOTA_MAP_P2.get(_gr_str_p2, _QUOTA_MAP_P2['BEAR_WATCH'])
                        # signal_combiner 가중치 1회 갱신
                        if hasattr(self, 'signal_combiner') and hasattr(self.signal_combiner, 'STRATEGY_WEIGHTS'):
                            self.signal_combiner.STRATEGY_WEIGHTS.update(_w)
                        # 쿼터/가중치 저장
                        self._strategy_quota  = _q
                        self._strategy_weight = _w
                        from loguru import logger as _lg
                        _lg.info(
                            f'[PHASE2-REGIME] 레짐 변경 → {_gr_str_p2} | '
                            f'SURGE {_w["SURGE_FASTENTRY"]:.1f}x({_q["SURGE_FASTENTRY"]}슬롯) '
                            f'Bollinger {_w["Bollinger_Squeeze"]:.1f}x({_q["Bollinger_Squeeze"]}슬롯) '
                            f'OrderBlock {_w["OrderBlock_SMC"]:.1f}x({_q["OrderBlock_SMC"]}슬롯) '
                            f'MACD {_w["MACD_Cross"]:.1f}x({_q["MACD_Cross"]}슬롯)'
                        )
                    else:
                        from loguru import logger as _lg
                        _lg.debug(f'[GlobalRegime] 유지: {self._global_regime}')
                else:
                    if not hasattr(self, "_global_regime"):
                        self._global_regime = None
            except Exception as _gr_e:
                from loguru import logger as _lg
                _lg.debug(f"[GlobalRegime] 갱신 실패: {_gr_e}")
                if not hasattr(self, "_global_regime"):
                    self._global_regime = None
            all_markets = await self._get_all_krw_markets()
            if not all_markets:
                return []

            fixed_markets = set(self.markets) if hasattr(self, "markets") else set()
            exclude       = set(cfg["exclude_markets"]) | fixed_markets
            scan_targets  = [m for m in all_markets if m not in exclude]
            logger.debug(f"[Scanner]  {len(scan_targets)}개 종목 스캔 시작")

            surge_candidates = []

            # Stage 1: WS 실시간 캐시로 사전 필터 (REST 호출 없음)
            _ws_scr  = getattr(self, '_market_change_rates', {})
            _ws_vol  = getattr(self, '_market_volumes_24h',  {})

            # 거래대금 상위 30% 임계값
            _vol_vals = [v for v in _ws_vol.values() if v > 0]
            _vol_p70  = sorted(_vol_vals)[int(len(_vol_vals)*0.70)] if len(_vol_vals) >= 10 else 0

            # Stage 1 후보 선별
            _stage1_scr  = []  # scr 있는 종목
            _stage1_miss = []  # WS 미수신 종목
            for _m in scan_targets:
                _scr_val = _ws_scr.get(_m, 0.0)
                _vol_val = _ws_vol.get(_m, 0.0)
                if _scr_val >= 0.02:
                    _stage1_scr.append((_m, abs(_scr_val)))
                elif _scr_val >= 0.01 and _vol_val >= _vol_p70:
                    _stage1_scr.append((_m, abs(_scr_val)))
                elif _scr_val == 0.0:
                    _stage1_miss.append((_m, _vol_val))

            # scr 내림차순 정렬, 최대 scr15개 + 미수신10개
            _stage1_scr.sort(key=lambda x: x[1], reverse=True)
            _stage1_miss.sort(key=lambda x: x[1], reverse=True)
            _final_targets = [x[0] for x in _stage1_scr[:15]] + [x[0] for x in _stage1_miss[:10]]

            logger.debug(
                f'[Scanner-2Stage] scr종목={len(_stage1_scr)}->'
                f'{min(len(_stage1_scr),15)}개 | '
                f'WS미수신={len(_stage1_miss)}->{min(len(_stage1_miss),10)}개 | '
                f'총 {len(_final_targets)}개 Stage2 진입'
            )
            if _stage1_scr:
                _top3 = [(x[0], round(x[1]*100, 1)) for x in _stage1_scr[:3]]
                logger.info(f'[SURGE-SCAN] 우선순위 상위3: {_top3}')

            # Stage 2: 후보만 REST 정밀 검증
            batch_size = 5
            for i in range(0, len(_final_targets), batch_size):
                batch   = _final_targets[i:i + batch_size]
                tasks   = [self._check_surge(m, cfg) for m in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for market, result in zip(batch, results):
                    if isinstance(result, Exception):
                        continue
                    if result and result.get('is_surge'):
                        surge_candidates.append(result)
                await asyncio.sleep(0.2)

            surge_candidates.sort(
                key=lambda x: x.get("score", 0), reverse=True
            )

            # ── _surge_cache 업데이트 (engine_buy.py에서 참조) ──────────
            if not hasattr(self, "_surge_cache"):
                self._surge_cache = {}
            # 만료된 캐시 제거 (5분)
            _now = time.time()
            self._surge_cache = {
                k: v for k, v in self._surge_cache.items()
                if _now - v.get("_ts", 0) < 300
            }
            # 신규 급등 코인 캐시 저장
            for _c in surge_candidates:
                _m = _c.get("market")
                if _m:
                    self._surge_cache[_m] = {**_c, "_ts": _now}
            logger.debug(f"[SurgeCache] {len(self._surge_cache)}개 코인 캐시")
            # SURGE 감지 즉시 _analyze_market 독립 트리거 (대기열 우회)
            import asyncio as _sg_aio
            _open_pos_now = set(self.portfolio.open_positions.keys())
            _buying_now_b = getattr(self, "_buying_markets", set())
            for _sg_m in list(self._surge_cache.keys()):
                if _sg_m not in _open_pos_now and _sg_m not in _buying_now_b:
                    logger.info(f"[SURGE-TRIGGER] {_sg_m} 즉시 분석 트리거")
                    _sg_aio.ensure_future(self._analyze_market(_sg_m))

            # [PENDING-QUEUE] 포지션 만석 시 surge 종목 대기열에 추가
            import time as _pq_time
            _open_cnt = len(self.portfolio.open_positions)
            _max_pos  = getattr(self.settings.trading, 'max_positions', 10)
            if not hasattr(self, '_pending_surge_queue'):
                from collections import deque as _dq
                self._pending_surge_queue = _dq(maxlen=5)
            for _sc in surge_candidates:
                _sm = _sc.get('market', '')
                _ss = _sc.get('score', 0.0)
                _already = any(x[0] == _sm for x in self._pending_surge_queue)
                _in_open  = _sm in self.portfolio.open_positions
                if _open_cnt >= _max_pos and not _already and not _in_open and _sm:
                    # scr 점수 비례 동적 TTL 계산
                    _scr_val  = getattr(self, '_market_change_rates', {}).get(_sm, 0.0)
                    _dyn_ttl  = (3600 if _scr_val >= 0.30
                                  else 1800 if _scr_val >= 0.10
                                  else 600)
                    _ttl_offset = max(0, 1800 - _dyn_ttl)
                    self._pending_surge_queue.appendleft(
                        (_sm, _ss, _pq_time.time() - _ttl_offset)
                    )
                    logger.info(
                        f'[PENDING-QUEUE] {_sm} 대기열 추가 '
                        f'(score={_ss:.3f}, scr={_scr_val*100:.1f}%, TTL={_dyn_ttl//60}분)'
                    )

            if self._pending_surge_queue:
                logger.debug(f'[PENDING-QUEUE] 현재 대기: {[x[0] for x in self._pending_surge_queue]}')

            new_markets     = []
            current_dynamic = set(self._dynamic_markets)

            for candidate in surge_candidates[: cfg["max_dynamic_coins"]]:
                market = candidate["market"]
                if market not in current_dynamic:
                    self._dynamic_markets.append(market)
                    new_markets.append(market)
                    logger.info(
                        f" [Scanner]  : {market} | "
                        f" ={candidate['vol_ratio']:.1f}x | "
                        f"={candidate['price_change']:.2%} | "
                        f"={candidate.get('trade_amount', 0):,.0f}"
                    )

            if len(self._dynamic_markets) > cfg["max_dynamic_coins"]:
                self._dynamic_markets = self._dynamic_markets[
                    -cfg["max_dynamic_coins"]:
                ]

            if new_markets:
                logger.info(
                    f"[Scanner]    {len(new_markets)}개 감시 추가: "
                    f"{new_markets} | 동적풀 총 {len(self._dynamic_markets)}개"
                )
            else:
                logger.debug(
                    f"[Scanner]     | "
                    f" : {len(self._dynamic_markets)}개"
                )
            return new_markets

        except Exception as e:
            logger.warning(f"[Scanner]  : {e}")
            return []


    async def _get_all_krw_markets(self) -> list:
        try:
            import aiohttp
            url = "https://api.upbit.com/v1/market/all"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, params={"isDetails": "false"}
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
            return [
                item["market"]
                for item in data
                if item["market"].startswith("KRW-")
            ]
        except Exception as e:
            logger.warning(f"[Scanner]    : {e}")
            return []


    async def _check_surge(self, market: str, cfg: dict) -> dict:
        """
        급등 감지 v2.1.0 - 전문 퀀트 수준
        타임프레임: 1분봉(진입) + 5분봉(맥락/BTC역행) + 15분봉(매집)
        실시간: ticks(체결강도) + orderbook(OBI)
        """
        try:
            from core.surge_detector import SurgeDetector

            # 1분봉 (거래량 폭발, 전고점 돌파) - 80개
            df_1m = await self.rest_collector.get_ohlcv(market, "minute1", 80)
            if df_1m is None or len(df_1m) < 20:
                return {"is_surge": False}

            # 5분봉 (BTC역행, 모멘텀) - 60개
            df_5m = None
            try:
                df_5m = await self.rest_collector.get_ohlcv(market, "minute5", 60)
            except Exception as _e:
                import logging as _lg
                _lg.getLogger("engine_cycle").debug(f"[WARN] engine_cycle 오류 무시: {_e}")
                pass

            # 15분봉 (세력 매집) - 40개
            df_15m = None
            try:
                df_15m = await self.rest_collector.get_ohlcv(market, "minute15", 40)
            except Exception as _e:
                import logging as _lg
                _lg.getLogger("engine_cycle").debug(f"[WARN] engine_cycle 오류 무시: {_e}")
                pass

            # BTC 5분봉 (역행 강도 분석)
            btc_df_5m = None
            if market != "KRW-BTC":
                try:
                    btc_df_5m = await self.rest_collector.get_ohlcv(
                        "KRW-BTC", "minute5", 30
                    )
                except Exception as _e:
                    import logging as _lg
                    _lg.getLogger("engine_cycle").debug(f"[WARN] engine_cycle 오류 무시: {_e}")
                    pass

            # 체결 내역 (체결 강도)
            ticks = None
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        "https://api.upbit.com/v1/trades/ticks",
                        params={"market": market, "count": 100},
                        timeout=aiohttp.ClientTimeout(total=2),
                    ) as r:
                        if r.status == 200:
                            ticks = await r.json()
            except Exception as _e:
                import logging as _lg
                _lg.getLogger("engine_cycle").debug(f"[WARN] engine_cycle 오류 무시: {_e}")
                pass

            # 오더북
            orderbook = None
            try:
                orderbook = self.cache_manager.get_orderbook(market)
            except Exception as _e:
                import logging as _lg
                _lg.getLogger("engine_cycle").debug(f"[WARN] engine_cycle 오류 무시: {_e}")
                pass

            # ticker (52주 고점)
            ticker = None
            try:
                ticker = self._market_prices_meta.get(market) if hasattr(self, "_market_prices_meta") else None
            except Exception as _e:
                import logging as _lg
                _lg.getLogger("engine_cycle").debug(f"[WARN] engine_cycle 오류 무시: {_e}")
                pass

            # 급등 분석 실행
            result = self._surge_detector.analyze(
                market=market,
                df_1m=df_1m,
                df_5m=df_5m,
                df_15m=df_15m,
                ticks=ticks,
                orderbook=orderbook,
                btc_df_5m=btc_df_5m,
                ticker=ticker,
            )

            if result.is_surge:
                return {
                    "is_surge":       True,
                    "market":         market,
                    "score":          result.score,
                    "grade":          result.grade,
                    "vol_ratio":      result.vol_ratio,
                    "price_change":   result.price_change_1m,
                    "price_change_5m": result.price_change_5m,
                    "breakout_pct":   result.breakout_pct,
                    "ob_pressure":    result.ob_pressure,
                    "obi":            result.obi,
                    "taker_ratio":    result.taker_buy_ratio,
                    "mtf_aligned":    result.mtf_aligned,
                    "pump_dump":      result.pump_dump_flag,
                    "reason":         result.reason,
                }
            return {"is_surge": False}

        except Exception as e:
            logger.debug(f"[_check_surge] {market}: {e}")
            return {"is_surge": False}
    async def _get_active_markets(self) -> list:
        fixed   = list(self.markets) if hasattr(self, "markets") else []
        dynamic = [m for m in self._dynamic_markets if m not in fixed]
        return fixed + dynamic

    # ── 백테스트 ─────────────────────────────────────────────────

    async def _run_backtest_v2(
        self,
        market: str,
        interval: str = "minute60",
        count: int    = 500,
        initial_capital: float = 1_000_000.0,
    ) -> dict:
        import numpy as np
        import pandas as pd

        FEE_RATE      = 0.0005
        SLIPPAGE_RATE = 0.0003

        try:
            df = await self.rest_collector.get_ohlcv(market, interval, count)
            if df is None or len(df) < 50:
                return {"error": "데이터 부족"}

            df        = df.reset_index(drop=True)
            capital   = initial_capital
            position  = 0.0
            entry_price = stop_loss = take_profit = 0.0
            trades    = []
            equity_curve = [capital]

            df["ema20"] = df["close"].ewm(span=20).mean()
            df["ema50"] = df["close"].ewm(span=50).mean()
            delta       = df["close"].diff()
            gain        = delta.clip(lower=0).rolling(14).mean()
            loss        = (-delta.clip(upper=0)).rolling(14).mean()
            df["rsi"]   = 100 - 100 / (1 + gain / (loss + 1e-9))
            ema12       = df["close"].ewm(span=12).mean()
            ema26       = df["close"].ewm(span=26).mean()
            df["macd"]      = ema12 - ema26
            df["macd_sig"]  = df["macd"].ewm(span=9).mean()
            close_prev = df["close"].shift(1)
            tr = pd.concat([
                df["high"] - df["low"],
                (df["high"] - close_prev).abs(),
                (df["low"]  - close_prev).abs(),
            ], axis=1).max(axis=1)
            df["atr"] = tr.rolling(14).mean()

            from risk.stop_loss.atr_stop import _get_profile_by_price
            _entry_est = float(df["close"].iloc[-1]) if len(df) > 0 else 1000
            _p         = _get_profile_by_price(_entry_est)
            profile    = {"atr_low": _p["min_sl"], "atr_high": _p["max_sl"]}

            for i in range(50, len(df)):
                row   = df.iloc[i]
                close = float(row["close"])
                atr   = float(row["atr"]) if not pd.isna(row["atr"]) else close * 0.02
                atr_pct = atr / close

                if position > 0:
                    equity_curve.append(capital + position * (close - entry_price))
                    if close <= stop_loss:
                        exit_price = stop_loss * (1 - SLIPPAGE_RATE)
                        pnl  = (exit_price - entry_price) * position
                        pnl -= (entry_price + exit_price) * position * FEE_RATE
                        capital += position * entry_price + pnl
                        trades.append({
                            "type": "LOSS", "pnl": pnl,
                            "pnl_pct": pnl / (position * entry_price),
                            "entry": entry_price, "exit": exit_price,
                        })
                        position = 0.0
                        continue
                    if close >= take_profit:
                        exit_price = take_profit * (1 - SLIPPAGE_RATE)
                        pnl  = (exit_price - entry_price) * position
                        pnl -= (entry_price + exit_price) * position * FEE_RATE
                        capital += position * entry_price + pnl
                        trades.append({
                            "type": "WIN", "pnl": pnl,
                            "pnl_pct": pnl / (position * entry_price),
                            "entry": entry_price, "exit": exit_price,
                        })
                        position = 0.0
                        continue

                if position == 0:
                    ema20    = float(row["ema20"])
                    ema50    = float(row["ema50"])
                    rsi      = float(row["rsi"])
                    macd     = float(row["macd"])
                    macd_sig = float(row["macd_sig"])
                    long_signals = sum([
                        ema20 > ema50,
                        50 < rsi < 70,
                        macd > macd_sig,
                        close > ema20,
                    ])
                    if long_signals >= 3:
                        if trades:
                            wins   = [t for t in trades if t["type"] == "WIN"]
                            losses = [t for t in trades if t["type"] == "LOSS"]
                            wr     = len(wins) / len(trades)
                            avg_w  = sum(t["pnl_pct"] for t in wins) / max(len(wins), 1)
                            avg_l  = abs(
                                sum(t["pnl_pct"] for t in losses)
                                / max(len(losses), 1)
                            )
                            rr     = avg_w / max(avg_l, 1e-9)
                            b      = max(0.5, rr)
                            p      = max(0.1, min(0.9, wr))
                            q      = 1 - p
                            kelly  = max(0.03, min(0.20, ((p * b - q) / b) * 0.5))
                        else:
                            kelly = 0.05

                        invest_amount = capital * kelly
                        entry_price   = close * (1 + SLIPPAGE_RATE)
                        position      = invest_amount / entry_price
                        capital      -= invest_amount

                        atr_low  = profile["atr_low"]
                        atr_high = profile["atr_high"]
                        if atr_pct < atr_low:
                            sl_mult, tp_mult = 1.5, 3.0
                        elif atr_pct < atr_high:
                            sl_mult, tp_mult = 2.0, 4.0
                        else:
                            sl_mult, tp_mult = 2.5, 5.0

                        stop_loss   = entry_price - atr * sl_mult
                        take_profit = entry_price + atr * tp_mult

            if position > 0:
                exit_price = float(df["close"].iloc[-1]) * (1 - SLIPPAGE_RATE)
                pnl  = (exit_price - entry_price) * position
                pnl -= (entry_price + exit_price) * position * FEE_RATE
                t    = "WIN" if pnl > 0 else "LOSS"
                trades.append({
                    "type": t, "pnl": pnl,
                    "pnl_pct": pnl / (position * entry_price),
                    "entry": entry_price, "exit": exit_price,
                })
                capital += position * entry_price + pnl

            if not trades:
                return {"market": market, "total_trades": 0, "error": "거래 없음"}

            total_trades  = len(trades)
            wins          = [t for t in trades if t["type"] == "WIN"]
            losses        = [t for t in trades if t["type"] == "LOSS"]
            win_rate      = len(wins) / total_trades
            total_profit  = sum(t["pnl"] for t in wins)
            total_loss    = abs(sum(t["pnl"] for t in losses))
            profit_factor = total_profit / max(total_loss, 1e-9)
            total_return  = (capital - initial_capital) / initial_capital
            returns       = [t["pnl_pct"] for t in trades]
            sharpe        = (
                (np.mean(returns) / (np.std(returns) + 1e-9)) * (252 ** 0.5)
                if len(returns) > 1 else 0.0
            )
            equity_arr = np.array(equity_curve)
            peak       = np.maximum.accumulate(equity_arr)
            drawdown   = (equity_arr - peak) / (peak + 1e-9)
            mdd        = float(drawdown.min())

            result = {
                "market":        market,
                "total_trades":  total_trades,
                "win_rate":      round(win_rate,      4),
                "profit_factor": round(profit_factor, 4),
                "total_return":  round(total_return,  4),
                "sharpe":        round(sharpe,        4),
                "mdd":           round(mdd,           4),
                "final_capital": round(capital,       0),
                "wins":          len(wins),
                "losses":        len(losses),
            }
            logger.info(
                f"[Backtest v2] {market} | "
                f"={total_trades} WR={win_rate:.1%} "
                f"PF={profit_factor:.2f} Return={total_return:.2%} "
                f"Sharpe={sharpe:.2f} MDD={mdd:.2%}"
            )
            return result

        except Exception as e:
            logger.warning(f"[Backtest v2] {market} : {e}")
            return {"market": market, "error": str(e)}


    async def _run_backtest_all(self) -> dict:
        markets = list(self.markets) if hasattr(self, "markets") else []
        if not markets:
            return {}
        logger.info(f"[Backtest v2]    | {len(markets)}개 코인")
        tasks  = [self._run_backtest_v2(m) for m in markets]
        raw    = await asyncio.gather(*tasks, return_exceptions=True)
        results = {}
        lines   = ["[백테스트 v2 결과]"]
        for market, result in zip(markets, raw):
            if isinstance(result, Exception):
                results[market] = {"error": str(result)}
            else:
                results[market] = result
                if "error" not in result:
                    lines.append(
                        f"{market}: WR={round(result['win_rate']*100,1)}% "
                        f"PF={result['profit_factor']} "
                        f"Ret={round(result['total_return']*100,1)}% "
                        f"MDD={round(result.get('mdd', 0)*100,1)}%"
                    )
        try:
            await self.telegram.send_message(" | ".join(lines))
        except Exception as _e:
            import logging as _lg
            _lg.getLogger("engine_cycle").debug(f"[WARN] engine_cycle 오류 무시: {_e}")
            pass
        logger.info(f"[Backtest v2]  | {len(results)}개 결과")
        return results

    # ── PPO 초기화 / 훈련 ────────────────────────────────────────
