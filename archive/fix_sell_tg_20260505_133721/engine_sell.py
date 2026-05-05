"""
core/engine_sell.py
─────────────────────────────────────────────────────────────
매도 실행 Mixin — 완전 재작성 (2026-05-01)

버그 수정:
  - TRACE warning 로그 제거
  - self.db_manager → self.db_manager 통일
  - except 블록 밖으로 LiveGuard/Telegram/log_trade 이동
  - _strat_name 스코프 버그 수정
  - notify_sell 시그니처 정확히 맞춤
─────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import datetime as _dt
from datetime import datetime
from execution.executor import ExecutionRequest, OrderSide
from utils.logger import setup_logger, log_trade
from loguru import logger
from core.engine_utils import _ceil_vol


def _entry_time_to_datetime(et) -> _dt.datetime:
    """entry_time을 datetime으로 변환 (str/float/int/datetime 모두 처리)"""
    if et is None:
        return _dt.datetime.now()
    if isinstance(et, _dt.datetime):
        return et
    if isinstance(et, str):
        try:
            return _dt.datetime.fromisoformat(et)
        except (TypeError, ValueError):
            return _dt.datetime.now()
    if isinstance(et, (int, float)):
        try:
            return _dt.datetime.fromtimestamp(et)
        except (TypeError, OSError):
            return _dt.datetime.now()
    return _dt.datetime.now()


def _fmt_price(price: float) -> str:
    """가격 크기에 따라 자동 소수점 포맷"""
    if price is None or price <= 0:
        return "0"
    if price >= 1:
        return f"{price:,.0f}"
    elif price >= 0.0001:
        return f"{price:.6f}"
    else:
        return f"{price:.8f}"


class EngineSellMixin:
    """매도 실행 관련 메서드 Mixin"""

    # ──────────────────────────────────────────────────────────
    # 1. 부분 청산
    # ──────────────────────────────────────────────────────────
    async def _execute_partial_sell(
        self, market: str, volume: float, current_price: float
    ):
        # ── [FIX-DUP] 부분청산 중복 방지 ─────────────────────────
        _dup_k = f'partial_{market}'
        if not hasattr(self, '_selling_markets'):
            self._selling_markets = set()
        if _dup_k in self._selling_markets:
            logger.debug(f'[DUP-SKIP] {_dup_k} 진행 중 → 중복 스킵')
            # PATCH-4: discard 제거 — 실행 중 태스크가 finally에서 제거
            return
        self._selling_markets.add(_dup_k)
        # 청산 완료 후 제거는 아래 DB 기록 직후에 처리됨
        # ─────────────────────────────────────────────────────────
        pos = self.portfolio.get_position(market)
        if not pos or volume <= 0:
            self._selling_markets.discard(_dup_k)  # [FIX-DUP] 부분청산 완료
            return

        _order_value   = volume * current_price
        _min_order     = self.settings.trading.min_order_amount
        _pos_total_val = getattr(pos, "volume", 0) * current_price

        if _order_value < _min_order:
            if _pos_total_val >= _min_order:
                logger.info(
                    f"부분청산 소액 → 전량매도 ({market}): "
                    f"부분=₩{_order_value:,.0f} < 최소=₩{_min_order:,.0f}"
                )
                await self._execute_sell(market, "소액포지션_전량매도", current_price)
            else:
                logger.warning(
                    f"전량청산 불가 ({market}): "
                    f"₩{_pos_total_val:,.0f} < ₩{_min_order:,.0f}"
                )
            self._selling_markets.discard(_dup_k)  # [FIX-DUP] 부분청산 완료
            return

        state = self.partial_exit.get_state(market)
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

        if not result or result.executed_price <= 0:
            logger.warning(f"[PARTIAL-SELL] {market} 체결 실패 (price=0)")
            self._selling_markets.discard(_dup_k)  # [FIX-DUP] 부분청산 완료
            return

        profit_rate = (
            (result.executed_price - pos.entry_price) / pos.entry_price * 100
            if pos.entry_price > 0 else 0.0
        )

        # PPO 업데이트
        try:
            if self.ppo_online_trainer is not None:
                _et = _entry_time_to_datetime(
                    getattr(pos, "entry_time", None)
                    or getattr(pos, "created_at", None)
                )
                _hold_h = (_dt.datetime.now() - _et).total_seconds() / 3600
                self.ppo_online_trainer.add_experience(
                    market=market, action=2,
                    profit_rate=profit_rate / 100,
                    hold_hours=_hold_h,
                )
        except Exception as e:
            logger.warning(f"[PARTIAL-SELL] PPO 업데이트 실패: {e}")

        # 포지션 수량 업데이트
        pos.volume -= volume

        # [FIX] SmartWallet record_sell → bot_qty 동기화
        try:
            _symbol_sw = market.replace("KRW-", "")
            _sold_qty_sw = float(
                getattr(result, "executed_volume",
                getattr(result, "quantity",
                getattr(result, "qty", volume)))
            )
            if _sold_qty_sw > 0:
                self._wallet.record_sell(
                    symbol=_symbol_sw,
                    sold_qty=_sold_qty_sw,
                    includes_dust=False,
                )
                logger.debug(
                    f"[PARTIAL-SW] {market} SmartWallet 갱신 | "
                    f"매도={_sold_qty_sw:.4f} | 잔량={pos.volume:.4f}"
                )
        except Exception as _sw_e:
            logger.warning(f"[PARTIAL-SW] SmartWallet record_sell 실패: {_sw_e}")

        if pos.volume <= 0:
            self.portfolio.close_position(
                market, result.executed_price, result.fee, reason
            )
            self.trailing_stop.remove_position(market)
            self.partial_exit.remove_position(market)

        # DB 저장
        _strat = getattr(pos, "strategy", "unknown") or "unknown"
        _mode  = getattr(self.settings, "mode", "paper")
        try:
            await self.db_manager.insert_trade({
                "timestamp":   _dt.datetime.now().isoformat(),
                "market":      market,
                "side":        "SELL",
                "price":       result.executed_price,
                "volume":      volume,
                "amount_krw":  volume * result.executed_price,
                "fee":         result.fee,
                "profit_rate": profit_rate,
                "strategy":    _strat,
                "reason":      reason,
                "mode":        _mode,
            })
        except Exception as e:
            logger.warning(f"[PARTIAL-SELL] DB insert_trade 실패: {e}")

        # positions 테이블 갱신
        try:
            _pos_now = self.portfolio.get_position(market)
            if _pos_now and getattr(_pos_now, "volume", 0) > 0:
                await self.db_manager.upsert_position({
                    "market":         market,
                    "entry_price":    getattr(_pos_now, "entry_price", 0),
                    "volume":         getattr(_pos_now, "volume", 0),
                    "amount_krw":     getattr(_pos_now, "amount_krw", 0),
                    "stop_loss":      getattr(_pos_now, "stop_loss", 0),
                    "take_profit":    getattr(_pos_now, "take_profit", 0),
                    "strategy":       getattr(_pos_now, "strategy", ""),
                    "entry_time":     getattr(_pos_now, "entry_time", None),
                    "pyramid_count":  getattr(_pos_now, "pyramid_count", 0),
                    "partial_exited": True,
                    "breakeven_set":  getattr(_pos_now, "breakeven_set", False),
                    "max_price":      getattr(_pos_now, "max_price",
                                      getattr(_pos_now, "entry_price", 0)),
                })
                logger.debug(f"[PARTIAL-SELL] {market} upsert 완료 (잔량={_pos_now.volume:.6f})")
            else:
                await self.db_manager.delete_position(market)
                logger.debug(f"[PARTIAL-SELL] {market} 전량청산 → positions 삭제")
        except Exception as e:
            logger.warning(f"[PARTIAL-SELL] DB upsert/delete 실패: {e}")

        log_trade(
            "PARTIAL_SELL", market, result.executed_price,
            volume * result.executed_price, reason, profit_rate
        )
        try:
            await self.telegram.notify_sell(
                market, result.executed_price, volume,
                profit_rate, reason
            )
        except Exception as e:
            logger.warning(f"[PARTIAL-SELL] Telegram 알림 실패: {e}")
        finally:
            self._selling_markets.discard(_dup_k)  # [FIX-DUP] 항상 제거 보장

    # ──────────────────────────────────────────────────────────
    # 2. 전량 매도 래퍼 (중복 방지)
    # ──────────────────────────────────────────────────────────
    async def _execute_sell(
        self, market: str, reason: str, current_price: float = None
    ):
        if not hasattr(self, "_selling_markets"):
            self._selling_markets = set()
        if market in self._selling_markets:
            logger.debug(f"[SELL] {market} 이미 매도 진행 중 → 중복 방지")
            return
        self._selling_markets.add(market)
        try:
            await self._execute_sell_inner(market, reason, current_price)
        finally:
            self._selling_markets.discard(market)

    # ──────────────────────────────────────────────────────────
    # 3. 전량 매도 핵심 로직
    # ──────────────────────────────────────────────────────────
    async def _execute_sell_inner(
        self, market: str, reason: str, current_price: float = None
    ):
        mode    = getattr(self.settings, "mode", "paper")
        _symbol = market.replace("KRW-", "")

        # ── 포지션 확인 ───────────────────────────────────────
        pos = self.portfolio.get_position(market)
        if not pos:
            pos = self.portfolio._positions.get(market)
        if not pos:
            logger.warning(f"[SELL] {market} 포지션 없음 → 취소")
            return

        # ── 매도 수량 결정 ────────────────────────────────────
        _raw_qty = float(
            getattr(pos, "volume", getattr(pos, "quantity", 0))
        )
        if _raw_qty <= 0:
            logger.warning(f"[SELL] {market} 수량=0 → 취소")
            return

        if mode == "live":
            # PATCH-6: 매도 전 실잔고 drift 보정
            try:
                _coin_p6 = market.split("-")[1] if "-" in market else market
                _real_bals_p6 = self._upbit.get_balances() if hasattr(self, '_upbit') else None
                if _real_bals_p6 is None:
                    # adapter를 통해 조회
                    try:
                        _real_bals_p6 = self.adapter._upbit.get_balances() or []
                    except Exception:
                        _real_bals_p6 = []
                _real_qty_p6 = next(
                    (
                        float(b.get("balance", 0)) + float(b.get("locked", 0))
                        for b in (_real_bals_p6 or [])
                        if b.get("currency") == _coin_p6
                    ),
                    0.0,
                )
                if _real_qty_p6 > 1e-10:
                    _drift_p6 = abs(_real_qty_p6 - _raw_qty) / max(_raw_qty, 1e-9)
                    if _drift_p6 > 0.005:  # 0.5% 이상 drift
                        logger.warning(
                            f"[SELL-DRIFT] {market} SmartWallet={_raw_qty:.8f} "
                            f"실잔고={_real_qty_p6:.8f} drift={_drift_p6*100:.2f}% "
                            f"→ SmartWallet 교정"
                        )
                        # SmartWallet bot_qty 교정 (FIFO 강제 동기화)
                        try:
                            _wallet_obj = self._wallet.get_wallet(_coin_p6)
                            if _wallet_obj is not None:
                                _diff_p6 = _real_qty_p6 - (
                                    _wallet_obj.bot_qty + _wallet_obj.dust_qty
                                )
                                if abs(_diff_p6) > 1e-8:
                                    _wallet_obj.dust_qty = max(
                                        0.0,
                                        _wallet_obj.dust_qty + _diff_p6,
                                    )
                        except Exception as _wfix_e:
                            logger.debug(f"[SELL-DRIFT] SmartWallet 교정 실패: {_wfix_e}")
            except Exception as _drift_e:
                logger.debug(f"[SELL-DRIFT] 실잔고 조회 실패: {_drift_e}")
            # ── PATCH-6 끝 ────────────────────────────────────────────────

            # live: SmartWallet 판단 우선
            try:
                _sell_dec = self._wallet.get_sell_decision(
                    symbol=_symbol,
                    current_price=current_price,
                    confidence=1.0,
                )
                if not _sell_dec.get("ok", True):
                    logger.warning(
                        f"[SELL] SmartWallet 거부 ({_symbol}): {_sell_dec.get('note','')}"
                    )
                    return
                _wallet_sell_qty  = _sell_dec.get("qty", _raw_qty)
                _wallet_incl_dust = _sell_dec.get("includes_dust", False)
            except Exception as e:
                logger.warning(f"[SELL] SmartWallet 오류 → 전량 매도로 진행: {e}")
                _wallet_sell_qty  = _ceil_vol(market, _raw_qty)
                _wallet_incl_dust = False
        else:
            # paper: 보유 수량 그대로
            _wallet_sell_qty  = _ceil_vol(market, _raw_qty)
            _wallet_incl_dust = False

        logger.info(
            f"[SELL] {market} | qty={_wallet_sell_qty:.6f} | "
            f"price={_fmt_price(current_price or 0)} | reason={reason}"
        )

        # ── 주문 실행 ─────────────────────────────────────────
        req = ExecutionRequest(
            market=market,
            side=OrderSide.SELL,
            amount_krw=0,
            volume=_wallet_sell_qty,
            reason=reason,
            strategy_name=getattr(pos, "strategy", "unknown") or "unknown",
        )
        result = await self.executor.execute(req)

        if not result or result.executed_price <= 0:
            logger.warning(f"[SELL] {market} 체결 실패 (price=0)")
            return

        # ── 수익 계산 ─────────────────────────────────────────
        _strat_name  = getattr(pos, "strategy", "unknown") or "unknown"
        _close_result = self.portfolio.close_position(
            market, result.executed_price, result.fee, reason
        )
        if _close_result is None:
            logger.error(f"[SELL] {market} 이미 청산된 포지션 → 건너뜀")
            return
        proceeds, profit_rate = _close_result
        profit_rate = float(profit_rate or 0)

        logger.info(
            f"[SELL DONE] {market} | "
            f"price={_fmt_price(result.executed_price)} | "
            f"profit={profit_rate:+.2f}% | reason={reason}"
        )
        # [CB-FIX] 매도 후 KRW 캐시 갱신
        try:
            self._cached_krw = getattr(self, "_cached_krw", 0.0) + float(proceeds)
            logger.debug(f"[CB] SELL 후 _cached_krw={self._cached_krw:,.0f}")
        except Exception:
            pass

        # ── DB 저장 ───────────────────────────────────────────
        try:
            await self.db_manager.insert_trade({
                "timestamp":   _dt.datetime.now().isoformat(),
                "market":      market,
                "side":        "SELL",
                "price":       result.executed_price,
                "volume":      result.executed_volume,
                "amount_krw":  proceeds,
                "fee":         result.fee if hasattr(result, "fee") else 0.0,
                "profit_rate": profit_rate,
                "strategy":    _strat_name,
                "reason":      reason,
                "mode":        mode,
            })
        except Exception as e:
            logger.warning(f"[SELL] DB insert_trade 실패: {e}")

        try:
            await self.db_manager.delete_position(market)
        except Exception as e:
            logger.warning(f"[SELL] DB delete_position 실패: {e}")

        # ── trailing/partial 제거 ─────────────────────────────
        try:
            self.trailing_stop.remove_position(market)
            self.partial_exit.remove_position(market)
        except Exception as e:
            logger.warning(f"[SELL] trailing/partial 제거 실패: {e}")

        # ── 손절 쿨다운 ───────────────────────────────────────
        _is_sl = (
            profit_rate < -0.5
            or any(kw in reason for kw in
                   ["손절", "stop", "SL", "트레일링", "ATR", "긴급"])
        )
        if _is_sl:
            if not hasattr(self, "_sl_cooldown"):
                self._sl_cooldown = {}
            _cd_until = (
                _dt.datetime.now() + _dt.timedelta(hours=4)
            ).isoformat()
            self._sl_cooldown[market] = _dt.datetime.fromisoformat(_cd_until)
            logger.info(
                f"[SELL] 손절쿨다운 {market}: 4h | "
                f"profit={profit_rate:.2f}% | reason={reason}"
            )
            try:
                await self.db_manager.set_state(
                    f"sl_cooldown_{market}", _cd_until
                )
            except Exception as e:
                logger.warning(f"[SELL] sl_cooldown DB 저장 실패(메모리는 유지): {e}")

        # ── 전략별 쿨다운 ─────────────────────────────────────
        try:
            _strat_cd_rules = {
                "Vol_Breakout":   {"max_loss": 2, "hours": 1},
                # [ST-1] "VWAP_Reversion": {"max_loss": 3, "hours": 1},  # 비활성화
            }
            if not hasattr(self, "_strat_consec_loss"):
                self._strat_consec_loss = {}
            if not hasattr(self, "_strat_cooldown_until"):
                self._strat_cooldown_until = {}
            if _strat_name in _strat_cd_rules:
                _rule = _strat_cd_rules[_strat_name]
                if profit_rate < 0:
                    self._strat_consec_loss[_strat_name] = (
                        self._strat_consec_loss.get(_strat_name, 0) + 1
                    )
                    _cnt = self._strat_consec_loss[_strat_name]
                    if _cnt >= _rule["max_loss"]:
                        _until = (
                            _dt.datetime.now()
                            + _dt.timedelta(hours=_rule["hours"])
                        )
                        self._strat_cooldown_until[_strat_name] = _until
                        logger.warning(
                            f"[SELL] {_strat_name} {_cnt}연속손실 → "
                            f"{_rule['hours']}h 냉각"
                        )
                        self._strat_consec_loss[_strat_name] = 0
                else:
                    self._strat_consec_loss[_strat_name] = 0
        except Exception as e:
            logger.warning(f"[SELL] 전략별 쿨다운 오류: {e}")

        # ── LiveGuard ─────────────────────────────────────────
        try:
            if hasattr(self, "live_guard") and self.live_guard is not None:
                await self.live_guard.on_trade_result(
                    profit_rate / 100.0, market
                )
                if profit_rate < 0:
                    _prev = getattr(self.live_guard, "_today_loss_pct", 0.0)
                    self.live_guard._today_loss_pct = (
                        _prev + profit_rate / 100.0
                    )
        except Exception as e:
            logger.warning(f"[SELL] LiveGuard 업데이트 실패: {e}")

        # ── PPO 업데이트 ──────────────────────────────────────
        try:
            if self.ppo_online_trainer is not None:
                _et = _entry_time_to_datetime(
                    getattr(pos, "entry_time", None)
                    or getattr(pos, "created_at", None)
                )
                _hold_h = (_dt.datetime.now() - _et).total_seconds() / 3600
                _pnl    = profit_rate / 100
                self.ppo_online_trainer.add_experience(
                    market=market, action=2,
                    profit_rate=_pnl, hold_hours=_hold_h,
                )
                _buf = (
                    self.ppo_online_trainer.get_buffer_stats()
                    if hasattr(self.ppo_online_trainer, "get_buffer_stats")
                    else {"size": 0, "max": 1000}
                )
                logger.info(
                    f"[SELL] PPO ({market}): "
                    f"PnL={_pnl*100:.2f}% | 보유={_hold_h:.1f}h | "
                    f"버퍼={_buf.get('size',0)}/{_buf.get('max',1000)}"
                )
        except Exception as e:
            logger.warning(f"[SELL] PPO 업데이트 실패: {e}")

        # ── 앙상블 가중치 업데이트 ────────────────────────────
        # [S-M1 FIX] self.ensemble_engine은 engine.py에 없음
        # 검증된 경로: self._v2_layer (V2EnsembleLayer)
        #   engine.py: self._v2_layer = V2EnsembleLayer()
        #   v2_layer.py L97: update_result() 정상 존재
        try:
            _v2_map = {
                "Order_Block":       "OrderBlock_SMC",
                "Vol_Breakout":      "VolBreakout",
                "MACD_Cross":        "MACD_Cross",
                "RSI_Divergence":    "RSI_Divergence",
                "Bollinger_Squeeze": "Bollinger_Squeeze",
                "ATR_Channel":       "ATR_Channel",
                # [ST-1] "VWAP_Reversion": "VWAP_Reversion",  # 비활성화
                "Supertrend":        "Supertrend",
                "ML_Ensemble":       "ML_Ensemble",
            }
            _v2_name = _v2_map.get(_strat_name, _strat_name)
            # _v2_layer: V2EnsembleLayer.update_result() 호출
            # profit_rate는 % 단위 → /100 하여 소수점으로 전달
            if getattr(self, "_v2_layer", None) is not None:
                self._v2_layer.update_result(
                    _v2_name, profit_rate / 100.0
                )
                logger.debug(
                    f"[SELL] V2Layer weight update | "
                    f"{_v2_name} | {profit_rate:+.2f}%"
                )
        except Exception as e:
            logger.warning(f"[SELL] Ensemble 업데이트 실패: {e}")

        # ── risk_manager ──────────────────────────────────────
        try:
            # [BUG-REAL-1-C FIX] profit_rate(%) 를 소수로 변환해 함께 전달
            self.risk_manager.record_trade_result(
                is_win=profit_rate > 0,
                profit_rate=profit_rate / 100.0,  # % → 소수 변환
            )
        except Exception as e:
            logger.warning(f"[SELL] risk_manager 업데이트 실패: {e}")

        # ── SmartWallet ───────────────────────────────────────
        try:
            _sold_qty = float(
                getattr(result, "executed_volume",
                getattr(result, "quantity",
                getattr(result, "qty", _wallet_sell_qty)))
            )
            if _sold_qty > 0:
                self._wallet.record_sell(
                    symbol=_symbol,
                    sold_qty=_sold_qty,
                    includes_dust=_wallet_incl_dust,
                )
        except Exception as e:
            logger.warning(f"[SELL] SmartWallet record_sell 실패: {e}")

        # ── 로그 + Telegram ───────────────────────────────────
        try:
            log_trade(
                "SELL", market, result.executed_price,
                proceeds, reason, profit_rate
            )
        except Exception as e:
            logger.warning(f"[SELL] log_trade 실패: {e}")

        try:
            await self.telegram.notify_sell(
                market, result.executed_price,
                result.executed_volume,
                profit_rate, reason
            )
        except Exception as e:
            logger.warning(f"[SELL] Telegram 알림 실패: {e}")
