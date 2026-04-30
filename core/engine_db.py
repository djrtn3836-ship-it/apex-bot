"""
core/engine_db.py
─────────────────────────────────────────────────────────────
DB 관련 Mixin

포함 메서드:
    _restore_positions_from_db : 재시작 시 DB에서 포지션 복원
    _restore_sl_cooldown       : SL 쿨다운 복원
    _save_initial_candles      : 초기 캔들 저장
    _initial_data_fetch        : 초기 데이터 수집
    _load_cooldown_from_db     : sell_cooldown DB 로드
    _save_cooldown_to_db       : sell_cooldown DB 저장
─────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import time
import asyncio
from datetime import datetime
from loguru import logger


class EngineDBMixin:
    """DB 포지션 복원, 쿨다운 저장/로드 관련 메서드 Mixin"""


    async def _restore_positions_from_db(self):
        """[FIX-POSITIONS-TABLE] positions 테이블 우선 복원 → fallback: trade_history"""
        try:
            # 1순위: positions 테이블에서 복원 (완전한 상태 포함)
            rows = await self.db_manager.get_all_positions()

            # positions 테이블이 비어있으면 trade_history fallback
            if not rows:
                import aiosqlite as _aio
                async with _aio.connect(str(self.db_manager.db_path)) as db:
                    db.row_factory = _aio.Row
                    cur = await db.execute("""
                        SELECT b.market, b.price, b.volume, b.amount_krw,
                               b.strategy, b.timestamp
                        FROM trade_history b
                        LEFT JOIN trade_history s
                            ON b.market = s.market
                           AND s.side   = 'SELL'
                           AND s.timestamp > b.timestamp
                        WHERE b.side = 'BUY'
                          AND b.mode IN ('paper', 'live')
                          AND s.id IS NULL
                        ORDER BY b.timestamp ASC
                    """)
                    th_rows = await cur.fetchall()

                for row in th_rows:
                    try:
                        from datetime import datetime as _dtt
                        _entry_unix = _dtt.fromisoformat(row["timestamp"]).timestamp()
                    except Exception:
                        import time as _t2
                        _entry_unix = _t2.time()
                    _is_surge = "SURGE" in (row["strategy"] or "")
                    _sl_cap   = 0.987 if _is_surge else 0.983
                    rows.append({
                        "market":        row["market"],
                        "entry_price":   float(row["price"] or 0),
                        "volume":        float(row["volume"] or 0),
                        "amount_krw":    float(row["amount_krw"] or 0),
                        "stop_loss":     float(row["price"] or 0) * _sl_cap,
                        "take_profit":   float(row["price"] or 0) * 1.03,
                        "strategy":      row["strategy"] or "unknown",
                        "entry_time":    _entry_unix,
                        "pyramid_count": 0,
                        "partial_exited": False,
                        "breakeven_set": False,
                        "max_price":     float(row["price"] or 0),
                    })
                if rows:
                    logger.info(f"[RESTORE] trade_history fallback으로 {len(rows)}개 포지션 복원")

            restored = 0
            total_invested = 0.0
            for pos in rows:
                try:
                    mkt         = pos["market"]
                    _price      = pos["entry_price"]
                    _volume     = pos["volume"]
                    _amount_krw = pos["amount_krw"]
                    _strategy   = pos["strategy"]
                    _entry_time = pos["entry_time"]
                    _sl         = pos["stop_loss"]
                    _tp         = pos["take_profit"]
                    _partial    = pos["partial_exited"]
                    _breakeven  = pos["breakeven_set"]
                    _max_price  = pos["max_price"] or _price

                    if self.portfolio.is_position_open(mkt):
                        continue
                    if _price <= 0 or _volume <= 0:
                        logger.warning(f"복원 스킵 ({mkt}): 가격/수량 없음")
                        continue

                    self.portfolio.open_position(
                        market=mkt, entry_price=_price, volume=_volume,
                        amount_krw=_amount_krw, strategy=_strategy,
                        stop_loss=_sl, take_profit=_tp, entry_time=_entry_time,
                    )
                    self.trailing_stop.add_position(
                        market=mkt, entry_price=_price,
                        initial_stop=_sl, atr=0.0,
                    )
                    if self.position_mgr_v2 is not None:
                        try:
                            from risk.position_manager_v2 import PositionV2
                            _pv2 = PositionV2(
                                market=mkt, entry_price=_price, volume=_volume,
                                amount_krw=_amount_krw, stop_loss=_sl,
                                take_profit=_tp, strategy=_strategy,
                            )
                            _pv2.partial_exited = _partial
                            _pv2.breakeven_set  = _breakeven
                            _pv2.max_price      = _max_price
                            self.position_mgr_v2.add_position(_pv2)
                        except Exception as _rv2_e:
                            logger.debug(f"M4 복원 오류: {_rv2_e}")

                    self.partial_exit.add_position(
                        market=mkt, entry_price=_price,
                        volume=_volume, take_profit=_tp,
                    )
                    if _partial:
                        try:
                            _exited = await self.db_manager.get_partial_exit_ratio(mkt)
                            if _exited and _exited > 0:
                                self.partial_exit.restore_executed_levels(mkt, _exited)
                                logger.info(f"부분청산 복원 | {mkt} | {_exited:.0%}")
                        except Exception as _pe_e:
                            logger.debug(f"부분청산 복원 오류 ({mkt}): {_pe_e}")

                    self.adapter._paper_balance["KRW"] = max(
                        0.0,
                        self.adapter._paper_balance.get("KRW", 1_000_000) - _amount_krw,
                    )
                    coin = mkt.replace("KRW-", "")
                    self.adapter._paper_balance[coin] = (
                        self.adapter._paper_balance.get(coin, 0.0) + _volume
                    )
                    restored       += 1
                    total_invested += _amount_krw
                    _held_h = (time.time() - _entry_time) / 3600
                    logger.info(
                        f"포지션 복원 | {mkt} | 진입가={_price:,.0f} | "
                        f"보유={_held_h:.1f}h | SL={_sl:,.1f} | TP={_tp:,.1f} | {_strategy}"
                    )

                except Exception as _row_e:
                    logger.warning(f"포지션 복원 실패 ({pos.get('market','?')}): {_row_e}")
                    continue

            if restored:
                logger.info(f"복원 완료: {restored}개 | 투자금=₩{total_invested:,.0f}")
                try:
                    _krw_cash = await self.adapter.get_balance("KRW")
                    _open_pos = {
                        m: {"volume": p.volume}
                        for m, p in self.portfolio.open_positions.items()
                    }
                    self.adapter.sync_paper_balance(_krw_cash, _open_pos)
                except Exception as _sync_e:
                    logger.debug(f"잔고 동기화 오류: {_sync_e}")
            else:
                logger.info("복원할 포지션 없음 (신규 시작)")

            # BEAR_REVERSAL 카운트 복원 [FIX-_aio2 미정의 변수 수정]
            try:
                import aiosqlite as _aio3
                _today_str = datetime.now().strftime("%Y-%m-%d")
                async with _aio3.connect(str(self.db_manager.db_path)) as _db3:
                    async with _db3.execute("""
                        SELECT COUNT(*) FROM trade_history
                        WHERE strategy LIKE '%BEAR_REVERSAL%'
                          AND side = 'BUY'
                          AND DATE(timestamp) = DATE('now','localtime')
                    """) as _cur3:
                        _row3 = await _cur3.fetchone()
                        _bear_today = int(_row3[0]) if _row3 and _row3[0] else 0
                _bear_count_key = f"_bear_rev_count_{_today_str}"
                setattr(self, _bear_count_key, _bear_today)
                _remain = max(0, 6 - _bear_today)
                logger.info(
                    f"BEAR_REVERSAL 카운트: 오늘 {_bear_today}회 → 잔여 {_remain}회"
                )
            except Exception as _br_e:
                logger.warning(f"BEAR_REVERSAL 카운트 복원 실패: {_br_e}")

        except Exception as e:
            import traceback
            logger.warning(f"포지션 복원 실패 (전체): {e}")
            logger.debug(traceback.format_exc())


    async def _restore_sl_cooldown(self):
        try:
            if not hasattr(self, "_sl_cooldown"):
                self._sl_cooldown = {}
            import datetime as _dt_cd
            if self.db_manager._conn is not None:
                async with self.db_manager._lock:
                    async with self.db_manager._conn.execute(
                        "SELECT key, value FROM bot_state "
                        "WHERE key LIKE 'sl_cooldown_%'"
                    ) as _cur:
                        _rows = await _cur.fetchall()
                restored_count = 0
                now = _dt_cd.datetime.now()
                for _key, _val in _rows:
                    try:
                        _until = _dt_cd.datetime.fromisoformat(_val)
                        if _until > now:
                            _mkt = _key.replace("sl_cooldown_", "", 1)
                            self._sl_cooldown[_mkt] = _until
                            _rem = int(
                                (_until - now).total_seconds() // 60
                            )
                            logger.info(
                                f"   ({_mkt}): {_rem}분 남음"
                            )
                            restored_count += 1
                        else:
                            await self.db_manager.delete_state(_key)
                    except Exception as _e:
                        logger.debug(f"   [{_key}]: {_e}")
                if restored_count:
                    logger.info(
                        f"    : {restored_count} "
                    )
                else:
                    logger.info("    ")
        except Exception as _e:
            logger.warning(f"    (): {_e}")


    async def _save_initial_candles(self):
        markets = self.settings.trading.target_markets
        saved   = 0
        for market in markets:
            try:
                df = await self.rest_collector.get_ohlcv(
                    market, interval="minute60", count=200
                )
                if df is not None and len(df) > 0:
                    self.cache_manager.set_ohlcv(market, "1h", df)
                    saved += 1
                    logger.debug(f"   | {market} | {len(df)}개")
            except Exception as e:
                logger.debug(f"   ({market}): {e}")
        logger.info(
            f"   NpyCache   | "
            f"{saved}/{len(markets)}개 코인"
        )


    async def _initial_data_fetch(self):
        logger.info("    ...")
        markets = self.settings.trading.target_markets
        tasks   = [
            self.rest_collector.get_ohlcv(m, "minute60", 200)
            for m in markets
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success = sum(
            1 for r in results
            if r is not None and not isinstance(r, Exception)
        )
        await self._save_initial_candles()
        logger.info(
            f"     ({success}/{len(markets)}개 성공)"
        )
        try:
            raw_balances = await self.adapter.get_balances()
            if isinstance(raw_balances, list) and raw_balances:
                self._wallet.scan_balances(raw_balances)
            self._wallet.print_status()
        except Exception as e:
            logger.warning(f"SmartWallet   : {e}")

    # ── 스케줄된 작업 ────────────────────────────────────────────

    def _load_cooldown_from_db(self) -> dict:
        """DB bot_state 테이블에서 sell cooldown 복원."""
        import json, sqlite3 as _sq
        result: dict = {}
        try:
            db_file = "database/apex_bot.db"
            conn = _sq.connect(db_file)
            cur  = conn.cursor()
            cur.execute("SELECT value FROM bot_state WHERE key='sell_cooldown' LIMIT 1")
            row = cur.fetchone()
            conn.close()
            if row:
                raw = json.loads(row[0])
                result = {k: datetime.fromisoformat(v) for k, v in raw.items()}
                print(f"  [COOLDOWN-RESTORE] {len(result)}개 복원")
        except Exception as e:
            print(f"  [COOLDOWN-RESTORE ERR] {e}")
        return result


    def _save_cooldown_to_db(self):
        """sell cooldown 데이터를 DB bot_state에 저장."""
        # [FIX-BUG1] import를 함수 내부로 이동하여 _sq NameError 해결
        import json as _json_cd, sqlite3 as _sq_cd

        # 만료된 sell_cooldown 자동 정리 (20분 초과)
        now_clean = datetime.now()
        self._sell_cooldown = {
            k: v for k, v in self._sell_cooldown.items()
            if (now_clean - v).total_seconds() < 1200
        }
        try:
            db_file = "database/apex_bot.db"
            data = {k: v.isoformat() for k, v in self._sell_cooldown.items()
                    if isinstance(v, datetime)}
            conn = _sq_cd.connect(db_file)
            cur  = conn.cursor()
            cur.execute("""
                INSERT INTO bot_state(key, value, updated_at)
                VALUES('sell_cooldown', ?, datetime('now','localtime'))
                ON CONFLICT(key) DO UPDATE
                SET value=excluded.value, updated_at=excluded.updated_at
            """, (_json_cd.dumps(data),))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"  [COOLDOWN-SAVE ERR] {e}")