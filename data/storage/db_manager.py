"""APEX BOT -  
SQLite (aiosqlite)   
  +   +"""
import asyncio
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from loguru import logger

try:
    import aiosqlite
    AIOSQLITE_OK = True
except ImportError:
    AIOSQLITE_OK = False
    logger.warning("aiosqlite  - DB  ")

from config.settings import get_settings


class DatabaseManager:
    """SQLite  
    - TRADE_HISTORY:  
    - CANDLE_DATA: OHLCV 
    - PERFORMANCE:  
    - SIGNAL_LOG:  
    - MODEL_METRICS: ML"""

    def __init__(self):
        self.settings = get_settings()
        self.db_path = self.settings.database.db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def initialize(self):
        """DB"""
        if not AIOSQLITE_OK:
            logger.warning("DB  -   ")
            return

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.db_path))
        # ── WAL 모드 + 성능 PRAGMA ─────────────────────────────
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute('PRAGMA journal_mode=WAL')
        await self._conn.execute('PRAGMA synchronous=NORMAL')
        await self._conn.execute('PRAGMA cache_size=-32000')   # 32MB 캐시
        await self._conn.execute('PRAGMA temp_store=MEMORY')
        await self._conn.execute('PRAGMA mmap_size=268435456') # 256MB mmap
        await self._conn.execute('PRAGMA wal_autocheckpoint=1000')
        await self._conn.commit()

        await self._create_tables()
        logger.info(f" DB : {self.db_path}")

    async def _create_tables(self):
        """_create_tables 실행"""
        schemas = [
            # 거래 내역
            """
            CREATE TABLE IF NOT EXISTS trade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                market TEXT NOT NULL,
                side TEXT NOT NULL,           -- BUY / SELL
                price REAL NOT NULL,
                volume REAL NOT NULL,
                amount_krw REAL NOT NULL,
                fee REAL DEFAULT 0,
                profit_rate REAL DEFAULT 0,
                strategy TEXT DEFAULT '',
                reason TEXT DEFAULT '',
                mode TEXT DEFAULT 'paper'     -- live / paper
            )
            """,
            # 일일 성과
            """
            CREATE TABLE IF NOT EXISTS daily_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                total_assets REAL,
                daily_pnl REAL,
                trade_count INTEGER,
                win_count INTEGER,
                max_drawdown REAL,
                sharpe_ratio REAL
            )
            """,
            # 신호 이력
            """
            CREATE TABLE IF NOT EXISTS signal_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                market TEXT NOT NULL,
                signal_type TEXT,
                score REAL,
                confidence REAL,
                strategies TEXT,
                regime TEXT,
                executed INTEGER DEFAULT 0
            )
            """,
            # ML 모델 성과
            """
            CREATE TABLE IF NOT EXISTS model_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                model_name TEXT,
                accuracy REAL,
                precision_val REAL,
                recall_val REAL,
                f1_score REAL,
                val_loss REAL
            )
            """,
            # 인덱스
            """
            CREATE TABLE IF NOT EXISTS bot_state (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now','localtime'))
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_bot_state_key ON bot_state(key)",
            "CREATE INDEX IF NOT EXISTS idx_trade_market ON trade_history(market)",
            "CREATE INDEX IF NOT EXISTS idx_trade_ts ON trade_history(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_signal_market ON signal_log(market)",
            # [FIX-POSITIONS-TABLE] 포지션 상태 영속화 테이블
            """
            CREATE TABLE IF NOT EXISTS positions (
                market          TEXT PRIMARY KEY,
                entry_price     REAL NOT NULL,
                volume          REAL NOT NULL,
                amount_krw      REAL NOT NULL,
                stop_loss       REAL DEFAULT 0,
                take_profit     REAL DEFAULT 0,
                strategy        TEXT DEFAULT '',
                entry_time      REAL NOT NULL,
                pyramid_count   INTEGER DEFAULT 0,
                partial_exited  INTEGER DEFAULT 0,
                breakeven_set   INTEGER DEFAULT 0,
                max_price       REAL DEFAULT 0,
                updated_at      TEXT DEFAULT (datetime('now','localtime'))
            )
            """,
            # trade_history entry_time 컬럼 (ALTER는 _migrate_schema에서 처리)
        ]

        async with self._lock:
            for sql in schemas:
                await self._conn.execute(sql)
            await self._conn.commit()

    # ── 거래 기록 ─────────────────────────────────────────────────
    async def insert_trade(self, trade: Dict) -> bool:
        """insert_trade 실행"""
        if not self._conn:
            return False
        try:
            async with self._lock:
                await self._conn.execute(
                    """
                    INSERT INTO trade_history
                    (timestamp, market, side, price, volume, amount_krw,
                     fee, profit_rate, strategy, reason, mode, entry_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trade.get("timestamp", datetime.now().isoformat()),
                        trade["market"],
                        trade["side"],
                        trade["price"],
                        trade["volume"],
                        trade["amount_krw"],
                        trade.get("fee", 0),
                        trade.get("profit_rate", 0),
                        trade.get("strategy", ""),
                        trade.get("reason", ""),
                        self.settings.mode,
                        trade.get("entry_time", trade.get("timestamp", datetime.now().isoformat())),  # [FIX-ENTRY-TIME]
                    )
                )
                await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"   : {e}")
            return False

    async def get_trades(self, market: str = None, limit: int = 100) -> List[Dict]:
        """get_trades 실행"""
        if not self._conn:
            return []
        try:
            if market:
                sql = "SELECT * FROM trade_history WHERE market=? ORDER BY timestamp DESC LIMIT ?"
                params = (market, limit)
            else:
                sql = "SELECT * FROM trade_history ORDER BY timestamp DESC LIMIT ?"
                params = (limit,)

            async with self._conn.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"   : {e}")
            return []

    # ── 성과 기록 ─────────────────────────────────────────────────
    async def save_daily_performance(self, perf: Dict) -> bool:
        """save_daily_performance 실행"""
        if not self._conn:
            return False
        try:
            async with self._lock:
                await self._conn.execute(
                    """
                    INSERT OR REPLACE INTO daily_performance
                    (date, total_assets, daily_pnl, trade_count, win_count,
                     max_drawdown, sharpe_ratio)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        perf.get("date", datetime.now().strftime("%Y-%m-%d")),
                        perf.get("total_assets", 0),
                        perf.get("daily_pnl", 0),
                        perf.get("trade_count", 0),
                        perf.get("win_count", 0),
                        perf.get("max_drawdown", 0),
                        perf.get("sharpe_ratio", 0),
                    )
                )
                await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"  : {e}")
            return False

    # ── 신호 기록 ─────────────────────────────────────────────────
    async def log_signal(self, signal: Dict) -> bool:
        """log_signal 실행"""
        if not self._conn:
            return False
        try:
            async with self._lock:
                await self._conn.execute(
                    """
                    INSERT INTO signal_log
                    (timestamp, market, signal_type, score, confidence,
                     strategies, regime, executed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        datetime.now().isoformat(),
                        signal.get("market", ""),
                        signal.get("signal_type", ""),
                        signal.get("score", 0),
                        signal.get("confidence", 0),
                        json.dumps(signal.get("strategies", [])),
                        signal.get("regime", ""),
                        1 if signal.get("executed", False) else 0,
                    )
                )
                await self._conn.commit()

                # signal_log 7일 이상 자동 삭제
                await self._conn.execute(
                    "DELETE FROM signal_log WHERE timestamp < datetime('now', '-7 days')"
                )
                await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"   : {e}")
            return False

    # ── ML 모델 메트릭 ────────────────────────────────────────────
    async def save_model_metrics(self, metrics: Dict) -> bool:
        """ML"""
        if not self._conn:
            return False
        try:
            async with self._lock:
                await self._conn.execute(
                    """
                    INSERT INTO model_metrics
                    (timestamp, model_name, accuracy, precision_val,
                     recall_val, f1_score, val_loss)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        datetime.now().isoformat(),
                        metrics.get("model_name", "ensemble"),
                        metrics.get("accuracy", 0),
                        metrics.get("precision", 0),
                        metrics.get("recall", 0),
                        metrics.get("f1", 0),
                        metrics.get("val_loss", 0),
                    )
                )
                await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"   : {e}")
            return False


    async def set_state(self, key: str, value: str) -> bool:
        """bot_state  key-value  (upsert)"""
        try:
            await self._conn.execute(
                """INSERT INTO bot_state (key, value, updated_at)
                   VALUES (?, ?, datetime('now','localtime'))
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                   updated_at=excluded.updated_at""",
                (key, value)
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"set_state  [{key}]: {e}")
            return False

    async def delete_state(self, key: str) -> None:
        """bot_state 테이블에서 key 삭제
        [DB-1 FIX] 새 연결 대신 self._conn 사용 — WAL 동시 쓰기 SQLITE_BUSY 방지"""
        if not self._conn:
            return
        try:
            async with self._lock:
                await self._conn.execute(
                    "DELETE FROM bot_state WHERE key = ?", (key,)
                )
                await self._conn.commit()
        except Exception as e:
            logger.error(f"delete_state 오류 [{key}]: {e}")

    async def get_state(self, key: str) -> str | None:
        """bot_state  key"""
        try:
            async with self._conn.execute(
                "SELECT value FROM bot_state WHERE key = ?", (key,)
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error(f"get_state  [{key}]: {e}")
            return None


    # ── positions 테이블 CRUD ─────────────────────────────────────
    # [FIX-POSITIONS-TABLE] 포지션 상태 영속화
    async def upsert_position(self, pos_data: dict) -> bool:
        """포지션 상태를 positions 테이블에 저장/갱신 (BUY 시 + 상태 변경 시 호출)"""
        if not self._conn:
            return False
        try:
            async with self._lock:
                await self._conn.execute("""
                    INSERT INTO positions
                    (market, entry_price, volume, amount_krw, stop_loss, take_profit,
                     strategy, entry_time, pyramid_count, partial_exited, breakeven_set,
                     max_price, updated_at)
                    VALUES (:market, :entry_price, :volume, :amount_krw, :stop_loss,
                            :take_profit, :strategy, :entry_time, :pyramid_count,
                            :partial_exited, :breakeven_set, :max_price,
                            datetime('now','localtime'))
                    ON CONFLICT(market) DO UPDATE SET
                        entry_price    = excluded.entry_price,
                        volume         = excluded.volume,
                        amount_krw     = excluded.amount_krw,
                        stop_loss      = excluded.stop_loss,
                        take_profit    = excluded.take_profit,
                        strategy       = excluded.strategy,
                        entry_time     = excluded.entry_time,
                        pyramid_count  = excluded.pyramid_count,
                        partial_exited = excluded.partial_exited,
                        breakeven_set  = excluded.breakeven_set,
                        max_price      = excluded.max_price,
                        updated_at     = datetime('now','localtime')
                """, {
                    "market":         pos_data.get("market", ""),
                    "entry_price":    pos_data.get("entry_price", 0),
                    "volume":         pos_data.get("volume", 0),
                    "amount_krw":     pos_data.get("amount_krw", 0),
                    "stop_loss":      pos_data.get("stop_loss", 0),
                    "take_profit":    pos_data.get("take_profit", 0),
                    "strategy":       pos_data.get("strategy", ""),
                    "entry_time":     pos_data.get("entry_time", 0),
                    "pyramid_count":  pos_data.get("pyramid_count", 0),
                    "partial_exited": int(pos_data.get("partial_exited", False)),
                    "breakeven_set":  int(pos_data.get("breakeven_set", False)),
                    "max_price":      pos_data.get("max_price", 0),
                })
                await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"upsert_position 오류: {e}")
            return False

    async def delete_position(self, market: str) -> bool:
        """SELL 완료 시 positions 테이블에서 포지션 삭제"""
        if not self._conn:
            return False
        try:
            async with self._lock:
                await self._conn.execute(
                    "DELETE FROM positions WHERE market = ?", (market,)
                )
                await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"delete_position 오류: {e}")
            return False

    async def get_all_positions(self) -> list:
        """재시작 시 positions 테이블에서 모든 열린 포지션 조회"""
        if not self._conn:
            return []
        try:
            async with self._conn.execute("""
                SELECT market, entry_price, volume, amount_krw, stop_loss,
                       take_profit, strategy, entry_time, pyramid_count,
                       partial_exited, breakeven_set, max_price
                FROM positions
                ORDER BY entry_time ASC
            """) as cur:
                rows = await cur.fetchall()
            return [
                {
                    "market":         r[0],
                    "entry_price":    r[1],
                    "volume":         r[2],
                    "amount_krw":     r[3],
                    "stop_loss":      r[4],
                    "take_profit":    r[5],
                    "strategy":       r[6],
                    "entry_time":     r[7],
                    "pyramid_count":  r[8],
                    "partial_exited": bool(r[9]),
                    "breakeven_set":  bool(r[10]),
                    "max_price":      r[11],
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"get_all_positions 오류: {e}")
            return []

    async def close(self):
        """DB"""
        if self._conn:
            await self._conn.close()
            logger.info("DB  ")

    async def get_partial_exit_ratio(self, market: str) -> float:
        """PARTIAL_SELL 비율 반환: trade_history volume 합산 / BUY volume"""
        try:
            today = __import__("datetime").date.today().isoformat()
            buy_vol  = 0.0
            sell_vol = 0.0
            async with self._conn.execute(
                """SELECT side, volume FROM trade_history
                   WHERE market=? AND timestamp LIKE ?
                   ORDER BY id ASC""",
                (market, f"{today}%")
            ) as cur:
                rows = await cur.fetchall()
            for side, vol in rows:
                if side == "BUY":
                    buy_vol += vol  # [DB-2 FIX] 누적 합산 (이전: 마지막 BUY만 사용)
                elif side in ("SELL", "PARTIAL_SELL"):
                    sell_vol += vol
            if buy_vol > 0:
                return sell_vol / buy_vol
        except Exception as _e:
            import logging as _lg
            _lg.getLogger("db_manager").debug(f"[WARN] db_manager 오류 무시: {_e}")
        return 0.0

