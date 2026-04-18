"""
core/engine_sell.py
─────────────────────────────────────────────────────────────
매도 실행 관련 Mixin

포함 메서드:
    _execute_partial_sell : 부분 청산 실행
    _execute_sell         : 전량 매도 래퍼 (중복 방지)
    _execute_sell_inner   : 전량 매도 실제 로직
─────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import datetime as _dt
import datetime as _ppo_dt
from datetime import datetime
from execution.executor import OrderExecutor, ExecutionRequest, OrderSide
from utils.logger import setup_logger, log_trade, log_signal, log_risk
from loguru import logger
from core.engine_utils import _ceil_vol


class EngineSellMixin:
    """매도 실행 관련 메서드 Mixin"""

    async def _execute_partial_sell(
        self, market: str, volume: float, current_price: float
    ):
        pos = self.portfolio.get_position(market)
        if not pos or volume <= 0:
            return

        _order_value   = volume * current_price
        _min_order     = self.settings.trading.min_order_amount
        _pos_total_val = getattr(pos, "volume", 0) * current_price

        if _order_value < _min_order:
            if _pos_total_val >= _min_order:
                logger.info(
                    f"    →   ({market}): "
                    f"부분=₩{_order_value:,.0f} < 최소=₩{_min_order:,.0f} | "
                    f"전체포지션=₩{_pos_total_val:,.0f}"
                )
                await self._execute_sell(
                    market, "소액포지션_전량매도", current_price
                )
            else:
                logger.warning(
                    f"      ({market}): "
                    f"₩{_pos_total_val:,.0f} < ₩{_min_order:,.0f}"
                )
            return

        state           = self.partial_exit.get_state(market)
        executed_levels = (
            sum(1 for lv in state.levels if lv.executed) if state else 0
        )
        reason = f"부분청산_step{executed_levels}"

        req = ExecutionRequest(
            market=market,
            side=OrderSide.SELL,
            amount_krw=0,
            volume=volume,
            reason=reason,
            strategy_name=(
                getattr(self.portfolio.get_position(market), "strategy", "unknown")
                or "unknown"
            ),
        )
        result = await self.executor.execute(req)

        if result.executed_price > 0:
            profit_rate = (
                (result.executed_price - pos.entry_price) / pos.entry_price * 100
            )

            try:
                if self.ppo_online_trainer is not None:
                    import datetime as _dt_ps
                    _entry_time = (
                        getattr(pos, "entry_time",  None)
                        or getattr(pos, "created_at", None)
                    )
                    _hold_hours = 0.0
                    if _entry_time:
                        if isinstance(_entry_time, str):
                            try:
                                _entry_time = _dt_ps.datetime.fromisoformat(_entry_time)
                            except (TypeError, ValueError):
                                _entry_time = _dt_ps.datetime.now()
                        elif isinstance(_entry_time, float):
                            _entry_time = _dt_ps.datetime.fromtimestamp(_entry_time)
                        _hold_hours = (
                            _dt_ps.datetime.now() - _entry_time
                        ).total_seconds() / 3600
                    self.ppo_online_trainer.add_experience(
                        market=market, action=2,
                        profit_rate=profit_rate, hold_hours=_hold_hours,
                    )
            except Exception as _ppo_ps_e:
                logger.debug(f"PPO PARTIAL SELL   : {_ppo_ps_e}")

            pos.volume -= volume
            if pos.volume <= 0:
                self.portfolio.close_position(
                    market, result.executed_price, result.fee, reason
                )
                self.trailing_stop.remove_position(market)
                self.partial_exit.remove_position(market)
            else:
                logger.info(
                    f"    | {market} | "
                    f"={result.executed_price:,.0f} | "
                    f"={volume:.6f} | "
                    f"={profit_rate:.2%} | "
                    f"={pos.volume:.6f}"
                )

            try:
                _strat = (
                    getattr(
                        self.portfolio.get_position(market), "strategy", "unknown"
                    ) or "unknown"
                )
                _mode = (
                    "paper"
                    if getattr(self.settings, "paper_mode", True)
                    else "live"
                )
                await self.db_manager.insert_trade({
                    "market":      market,
                    "side":        "SELL",
                    "price":       result.executed_price,
                    "volume":      volume,
                    "amount_krw":  volume * result.executed_price,
                    "fee":         result.fee,
                    "profit_rate": profit_rate,  # [FIX] 이미 % 단위 (* 100 제거)
                    "strategy":    _strat,
                    "reason":      reason,
                    "mode":        _mode,
                    "timestamp":   _dt.datetime.now().isoformat(),
                })
                logger.debug(f"  DB   ({market}): {reason}")
            except Exception as _db_e:
                logger.debug(f" DB   ({market}): {_db_e}")

            log_trade(
                "PARTIAL_SELL", market, result.executed_price,
                volume * result.executed_price, reason, profit_rate
            )
            await self.telegram.notify_sell(
                market, result.executed_price, volume,
                profit_rate * 100, reason  # [FIX] 소수->% 변환
            )

    # ── 전량 매도 (래퍼) ────────────────────────────────────────

    async def _execute_sell(
        self, market: str, reason: str, current_price: float = None
    ):
        if market in self._selling_markets:
            logger.debug(f"   ({market})")
            return
        self._selling_markets.add(market)
        try:
            await self._execute_sell_inner(market, reason, current_price)
        finally:
            self._selling_markets.discard(market)


    async def _execute_sell_inner(
        self, market: str, reason: str, current_price: float = None
    ):
        _symbol     = market.replace("KRW-", "")
        _confidence = 1.0
        _sell_dec   = self._wallet.get_sell_decision(
            symbol=_symbol, current_price=current_price, confidence=_confidence,
        )

        if getattr(self.settings, "paper_mode", True):
            pos           = self.portfolio._positions.get(market)
            _raw_qty      = float(
                getattr(pos, "volume",
                getattr(pos, "quantity", 0))
            ) if pos else 0.0
            _wallet_sell_qty  = _ceil_vol(market, _raw_qty)
            _wallet_incl_dust = False
            # [TRACE] paper SELL 진입 확인
            logger.warning(
                f"[PAPER-SELL T1] {market} | pos={pos} | "
                f"raw_qty={_raw_qty} | sell_qty={_wallet_sell_qty}"
            )
        else:
            if not _sell_dec["ok"]:
                logger.warning(
                    f" SmartWallet   ({_symbol}): {_sell_dec['note']}"
                )
                return
            _wallet_sell_qty  = _sell_dec["qty"]
            _wallet_incl_dust = _sell_dec["includes_dust"]
            logger.info(
                f" SmartWallet   | {_symbol} | "
                f"={_wallet_sell_qty:.8f} | {_sell_dec['note']}"
            )

        pos = self.portfolio.get_position(market)
        # [TRACE] get_position 결과 확인
        logger.warning(f"[PAPER-SELL T2] {market} | get_position={pos}")
        if not pos:
            # paper 모드에서 _positions 직접 재시도
            pos = self.portfolio._positions.get(market)
            logger.warning(f"[PAPER-SELL T3] {market} | _positions 직접조회={pos}")
            if not pos:
                logger.warning(f"[PAPER-SELL BLOCK] {market} | 포지션 없음 → SELL 취소")
                return

        req = ExecutionRequest(
            market=market,
            side=OrderSide.SELL,
            amount_krw=0,
            volume=pos.volume,
            reason=reason,
            strategy_name=getattr(pos, "strategy", "unknown") or "unknown",
        )
        result = await self.executor.execute(req)

        if result.executed_price > 0:
            proceeds, profit_rate = self.portfolio.close_position(
                market, result.executed_price, result.fee, reason
            )

            try:
                import asyncio as _asyncio
                _trade = {
                    "timestamp":   _dt.datetime.now().isoformat(),
                    "market":      market,
                    "side":        "SELL",
                    "price":       result.executed_price,
                    "volume":      result.executed_volume,
                    "amount_krw":  proceeds,
                    "fee":         result.fee if hasattr(result, "fee") else 0.0,
                    # [FIX2] close_position 반환값은 이미 % 단위 → * 100 제거
                    "profit_rate": profit_rate * 100,  # [FIX-FINAL] 소수→% 변환
                    "strategy":    getattr(pos, "strategy", "unknown"),
                    "reason":      reason,
                    "mode":        "paper",
                }
                if _asyncio.get_event_loop().is_running():
                    _asyncio.ensure_future(
                        self.executor.db_manager.insert_trade(_trade)
                    )
                else:
                    _asyncio.get_event_loop().run_until_complete(
                        self.executor.db_manager.insert_trade(_trade)
                    )
                logger.info(
                    f"[DB-SELL] {market} "
                    f"profit={profit_rate:.2f}%  "
                )
                # [FIX A-2] sell cooldown 기록
                import time as _time_a
                if not hasattr(self, "_sell_cooldown"):
                    self._sell_cooldown = {}
                self._sell_cooldown[market] = datetime.now()  # [FIX-CD] datetime으로 통일
                logger.debug(f"[COOLDOWN-SET] {market}: 매도 시각 기록 완료")
                self._save_cooldown_to_db()  # [FIX1] DB 저장
            except Exception as _e:
                logger.warning(f"[DB-SELL]  : {_e}")

            try:
                if self.ppo_online_trainer is not None:
                    _pos_ref = pos
                    _etime   = (
                        getattr(_pos_ref, "entry_time",  None)
                        or getattr(_pos_ref, "created_at", None)
                    )
                    _hold_h  = 0.0
                    if _etime:
                        if isinstance(_etime, str):
                            try:
                                _etime = _ppo_dt.datetime.fromisoformat(_etime)
                            except (TypeError, ValueError):
                                _etime = _ppo_dt.datetime.now()
                        elif isinstance(_etime, (int, float)):
                            try:
                                _etime = _ppo_dt.datetime.fromtimestamp(_etime)
                            except (TypeError, OSError):
                                _etime = _ppo_dt.datetime.now()
                        elif not isinstance(_etime, _ppo_dt.datetime):
                            _etime = _ppo_dt.datetime.now()
                        _hold_h = (
                            _ppo_dt.datetime.now() - _etime
                        ).total_seconds() / 3600
                    _pnl = profit_rate / 100
                    self.ppo_online_trainer.add_experience(
                        market=market, action=2,
                        profit_rate=_pnl, hold_hours=_hold_h,
                    )
                    _buf = self.ppo_online_trainer.get_buffer_stats()
                    logger.info(
                        f" PPO   ({market}): "
                        f"PnL={_pnl*100:.2f}% | 보유={_hold_h:.1f}h | "
                        f"버퍼={_buf.get('size',0)}/{_buf.get('max',1000)}"
                    )
            except Exception as _ppo_e:
                logger.debug(f"PPO SELL   : {_ppo_e}")

            self.trailing_stop.remove_position(market)
            self.partial_exit.remove_position(market)

            # ✅ FIX: reason 문자열 의존 → profit_rate 수치 기반으로 변경
            # 손실(-0.5% 이상) 또는 reason에 손절 키워드 포함 시 쿨다운 적용
            _is_sl = (
                profit_rate < -0.005
                or "손절" in reason
                or "stop" in reason.lower()
                or "트레일링" in reason
                or "ATR" in reason
                or "SL" in reason
                or "긴급" in reason
            )
            if _is_sl:
                if not hasattr(self, '_sl_cooldown'):
                    self._sl_cooldown = {}
                _cd_until = (
                    _dt.datetime.now() + _dt.timedelta(hours=4)
                ).isoformat()
                self._sl_cooldown[market] = _dt.datetime.fromisoformat(_cd_until)
                logger.info(
                    f'손절쿨다운 ({market}): 4시간 재매수 금지'
                    f' | profit={profit_rate:.4f} | reason={reason}'
                )
                try:
                    await self.db_manager.set_state(
                        f'sl_cooldown_{market}', _cd_until
                    )
                    logger.debug(f'[OK] sl_cooldown DB 저장: {market} until {_cd_until[:19]}')
                except Exception as _cde:
                    logger.warning(f'sl_cooldown DB 저장 실패 ({market}): {_cde}')
                    # DB 실패해도 메모리 쿨다운은 유지됨

            self.risk_manager.record_trade_result(profit_rate > 0)
            log_trade(
                "SELL", market, result.executed_price,
                proceeds, reason, profit_rate
            )
            await self.telegram.notify_sell(
                market, result.executed_price, result.executed_volume,
                profit_rate * 100, reason  # [FIX] 소수->% 변환
            )

        try:
            _sold_qty = float(getattr(result, "executed_volume",
                        getattr(result, "quantity",
                        getattr(result, "qty", _wallet_sell_qty))))
            if _sold_qty > 0:
                self._wallet.record_sell(
                    symbol=_symbol, sold_qty=_sold_qty,
                    includes_dust=_wallet_incl_dust,
                )
        except Exception as _we:
            logger.debug(f"SmartWallet record_sell : {_we}")
    # ── 초기화 헬퍼 ─────────────────────────────────────────────