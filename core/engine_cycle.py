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
                self.settings.risk, "daily_loss_limit", 0.05
            )

            krw = (
                self.adapter._paper_balance.get("KRW", 0)
                if self.adapter.is_paper else 0
            )
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
                        if await self._check_circuit_breaker():
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
        # [MDD-L3] 포트폴리오 서킷브레이커
        try:
            from datetime import datetime as _dt
            _today = _dt.now().strftime("%Y-%m-%d")
            _daily_loss_key = f"_daily_loss_{_today}"
            _daily_loss = getattr(self, _daily_loss_key, 0.0)
            _krw_bal = getattr(self, "_krw_balance", 0)
            _loss_limit = _krw_bal * 0.02  # 일일 2% 한도
            if _daily_loss < -_loss_limit and _loss_limit > 0:
                logger.warning(
                    f"[MDD-L3] 🚨 서킷브레이커 발동! "
                    f"일일손실 ₩{abs(_daily_loss):,.0f} > "
                    f"한도 ₩{_loss_limit:,.0f} (2%) → 신규매수 중단"
                )
                self._circuit_breaker_active = True
            else:
                self._circuit_breaker_active = False
            # [LiveGuard-C] 오늘 손실률 → live_guard 동기화
            if hasattr(self, 'live_guard') and self.live_guard is not None:
                try:
                    _krw_now = getattr(self, '_krw_balance', 1) or 1
                    self.live_guard._today_loss_pct = _daily_loss / _krw_now
                except Exception:
                    pass
            if _ml_df is None or len(_ml_df) < 10:
                try:
                    _ml_df = self.cache_manager.get_candles(_ml_market, "1d")
                except Exception as _e:
                    import logging as _lg
                    _lg.getLogger("engine_cycle").debug(f"[WARN] engine_cycle 오류 무시: {_e}")
                    pass
            if _ml_df is None or len(_ml_df) < 10:
                for _attr in ["_df_cache", "_candle_cache", "_ohlcv_cache"]:
                    _cache = getattr(self, _attr, None)
                    if _cache and isinstance(_cache, dict):
                        _ml_df = (
                            _cache.get(f"{_ml_market}-1h")
                            or _cache.get(_ml_market)
                        )
                        if _ml_df is not None:
                            break
            if _ml_df is not None and len(_ml_df) >= 50:
                _ml_result = await self._get_ml_prediction(_ml_market, _ml_df)
                if _ml_result:
                    from monitoring.dashboard import dashboard_state
                    _sig  = _ml_result.get("signal",     "HOLD")
                    _conf = _ml_result.get("confidence", 0.0)
                    _bp   = _ml_result.get("buy_prob",   0.0)
                    _sp   = _ml_result.get("sell_prob",  0.0)
                    dashboard_state.signals["ml_prediction"] = {
                        "signal":     _sig,
                        "confidence": round(float(_conf), 3),
                        "buy_prob":   round(float(_bp),   3),
                        "sell_prob":  round(float(_sp),   3),
                        "market":     _ml_market,
                    }
                    dashboard_state.signals["ml_predictions"] = {
                        _ml_market: dashboard_state.signals["ml_prediction"]
                    }
        except Exception as _e:
            import logging as _lg
            _lg.getLogger("engine_cycle").debug(f"[WARN] engine_cycle 오류 무시: {_e}")
            pass

        # [REMOVED] _update_dashboard_state — krw/total_value 미정의 제거
    # ── 시간기반 강제청산 ────────────────────────────────────────

    async def _check_time_based_exits(self) -> None:
        now     = datetime.now()
        markets = list(self.portfolio.open_positions.keys())

        for market in markets:
            try:
                pos = self.portfolio.get_position(market)
                if not pos:
                    continue
                current_price = self._market_prices.get(market)
                if not current_price or current_price <= 0:
                    continue
                entry_time = (
                    getattr(pos, "entry_time",  None)
                    or getattr(pos, "created_at", None)
                )
                if entry_time is None:
                    continue
                if isinstance(entry_time, str):
                    try:
                        entry_time = datetime.fromisoformat(entry_time)
                    except Exception:
                        continue

                elif isinstance(entry_time, float):
                    entry_time = datetime.fromtimestamp(entry_time)
                held_hours  = (now - entry_time).total_seconds() / 3600
                profit_rate = (current_price - pos.entry_price) / pos.entry_price

                if held_hours >= 72:
                    logger.info(
                        f" 72h  ({market}): "
                        f"보유={held_hours:.1f}h | 수익={profit_rate*100:.2f}%"
                    )
                    await self._execute_sell(market, "시간초과_72h_강제청산", current_price)
                    continue

                if held_hours >= 48 and -0.01 <= profit_rate <= 0.01:
                    logger.info(
                        f" 48h  ({market}): "
                        f"보유={held_hours:.1f}h | 수익={profit_rate*100:.2f}%"
                    )
                    await self._execute_sell(market, "횡보청산_48h", current_price)
                    continue

                if held_hours >= 24 and profit_rate <= -0.02:
                    logger.info(
                        f" 24h  ({market}): "
                        f"보유={held_hours:.1f}h | 수익={profit_rate*100:.2f}%"
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
                current_price = self._market_prices.get(market)
                if not current_price:
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
                        _sl_levels  = self.atr_stop.get_dynamic_levels(
                            _df_pos, entry_price, current_price, _profit_pct
                        )
                        basic_sl = max(_sl_levels.stop_loss, entry_price * 0.97)  # [FIX-SL] ATR cap -3%
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

            current_price = self._market_prices.get(market, 0)
            pnl_pct = (
                (current_price - entry_price) / entry_price * 100
                if entry_price > 0 else 0.0
            )

            logger.debug(
                f"   | {market} | "
                f"ML={signal}({confidence:.2f}) | PnL={pnl_pct:+.2f}%"
            )

            if entry_price > 0 and current_price > 0 and _candle_len >= 20:
                try:
                    _profit_pct = (current_price - entry_price) / entry_price
                    _atr_levels = self.atr_stop.get_dynamic_levels(
                        candles, entry_price, current_price, _profit_pct
                    )
                    _basic_sl = _atr_levels.stop_loss
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
                            market, f"ATR손절_{_loss_pct:.1f}%", current_price
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


            # [FIX] 최소 보유 30분 - 매수 직후 손절 방지
            _pos_et = getattr(pos, "entry_time", None) or getattr(pos, "created_at", None)
            _held_min = 0
            import datetime as _dt_hold  # [FIX] _dt_hold import
            if _pos_et:
                try:
                    if isinstance(_pos_et, str):
                        _et = _dt_hold.datetime.fromisoformat(_pos_et)
                    elif isinstance(_pos_et, (int, float)):
                        _et = _dt_hold.datetime.fromtimestamp(_pos_et)  # [FIX] float Unix timestamp 처리
                    else:
                        _et = _pos_et
                    _held_min = (_dt_hold.datetime.now() - _et).total_seconds() / 60
                except Exception:
                    _held_min = 999
            if _held_min < 10 and pnl_pct > -2.0:  # [FIX] 30→10분으로 완화
                logger.debug(f"  ({market}): 최소보유 미달 {_held_min:.1f}min < 10min, SELL 차단")
            elif (
                (signal == "SELL" and confidence >= 0.65 and pnl_pct >= 0.5) or   # ML익절 최소 +0.5%
                (signal == "SELL" and confidence >= 0.65 and pnl_pct <= -1.5) or  # ML손절 -1.5%
                (confidence >= 0.65 and pnl_pct >= 1.5) or                        # 강한 수익 익절
                (confidence >= 0.65 and pnl_pct <= -1.5) or                       # [FIX-RR] -2.0 → -1.5
                (pnl_pct >= 3.0) or                                                # 3% 무조건 익절
                (pnl_pct <= -2.5 and (confidence >= 0.50 or _held_min >= 720)) or # [FIX] 12h+ 보유시 confidence 완화
                (pnl_pct >= self._time_based_tp_threshold(market))                 # 시간 기반 익절
            ):
                logger.info(
                    f" ML   | {market} | "
                    f"={confidence:.2f} | ={pnl_pct:+.2f}%"
                )
                await self._execute_sell(
                    market, f"ML익절_{pnl_pct:.1f}%", current_price
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
            # [FIX] load_optimized_params가 전체 dict를 반환할 경우 strategies 키 파싱
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
        from strategies.mean_reversion.vwap_reversion import VWAPReversionStrategy
        from strategies.volatility.vol_breakout import VolBreakoutStrategy
        from strategies.volatility.atr_channel import ATRChannelStrategy
        from strategies.market_structure.order_block import OrderBlockStrategy

        strategies = [
            MACDCrossStrategy(), RSIDivergenceStrategy(), SupertrendStrategy(),
            BollingerSqueezeStrategy(), VWAPReversionStrategy(),
            VolBreakoutStrategy(), ATRChannelStrategy(), OrderBlockStrategy(),
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
            all_markets = await self._get_all_krw_markets()
            if not all_markets:
                return []

            fixed_markets = set(self.markets) if hasattr(self, "markets") else set()
            exclude       = set(cfg["exclude_markets"]) | fixed_markets
            scan_targets  = [m for m in all_markets if m not in exclude]
            logger.debug(f"[Scanner]  {len(scan_targets)}개 종목 스캔 시작")

            surge_candidates = []
            batch_size       = 20
            for i in range(0, len(scan_targets), batch_size):
                batch   = scan_targets[i:i + batch_size]
                tasks   = [self._check_surge(m, cfg) for m in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for market, result in zip(batch, results):
                    if isinstance(result, Exception):
                        continue
                    if result and result.get("is_surge"):
                        surge_candidates.append(result)
                await asyncio.sleep(0.5)

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
            if not hasattr(self, "_surge_detector"):
                self._surge_detector = SurgeDetector()

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