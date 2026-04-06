"""
APEX BOT - 데이터베이스 관리자
SQLite (aiosqlite) 기반 비동기 스토리지
거래 기록 + 캔들 데이터 + 성과 이력 영구 저장
"""
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
    logger.warning("aiosqlite 미설치 - DB 저장 비활성화")

from config.settings import get_settings


class DatabaseManager:
    """
    비동기 SQLite 데이터베이스 관리
    - TRADE_HISTORY: 거래 내역
    - CANDLE_DATA: OHLCV 캐시
    - PERFORMANCE: 일일 성과
    - SIGNAL_LOG: 신호 이력
    - MODEL_METRICS: ML 모델 성과
    """

    def __init__(self):
        self.settings = get_settings()
        self.db_path = self.settings.database.db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def initialize(self):
        """DB 초기화 및 테이블 생성"""
        if not AIOSQLITE_OK:
            logger.warning("DB 비활성화 - 메모리 모드로 실행")
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
        logger.info(f"✅ DB 초기화: {self.db_path}")

    async def _create_tables(self):
        """테이블 스키마 생성"""
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
        ]

        async with self._lock:
            for sql in schemas:
                await self._conn.execute(sql)
            await self._conn.commit()

    # ── 거래 기록 ─────────────────────────────────────────────────
    async def insert_trade(self, trade: Dict) -> bool:
        """거래 내역 저장"""
        if not self._conn:
            return False
        try:
            async with self._lock:
                await self._conn.execute(
                    """
                    INSERT INTO trade_history
                    (timestamp, market, side, price, volume, amount_krw,
                     fee, profit_rate, strategy, reason, mode)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    )
                )
                await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"거래 기록 저장 실패: {e}")
            return False

    async def get_trades(self, market: str = None, limit: int = 100) -> List[Dict]:
        """거래 내역 조회"""
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
            logger.error(f"거래 내역 조회 실패: {e}")
            return []

    # ── 성과 기록 ─────────────────────────────────────────────────
    async def save_daily_performance(self, perf: Dict) -> bool:
        """일일 성과 저장"""
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
            logger.error(f"성과 저장 실패: {e}")
            return False

    # ── 신호 기록 ─────────────────────────────────────────────────
    async def log_signal(self, signal: Dict) -> bool:
        """신호 이력 저장"""
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
            return True
        except Exception as e:
            logger.error(f"신호 기록 저장 실패: {e}")
            return False

    # ── ML 모델 메트릭 ────────────────────────────────────────────
    async def save_model_metrics(self, metrics: Dict) -> bool:
        """ML 모델 성과 저장"""
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
            logger.error(f"모델 메트릭 저장 실패: {e}")
            return False


    async def set_state(self, key: str, value: str) -> bool:
        """bot_state 테이블에 key-value 저장 (upsert)"""
        try:
            await self.db.execute(
                """INSERT INTO bot_state (key, value, updated_at)
                   VALUES (?, ?, datetime('now','localtime'))
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                   updated_at=excluded.updated_at""",
                (key, value)
            )
            await self.db.commit()
            return True
        except Exception as e:
            logger.error(f"set_state 오류 [{key}]: {e}")
            return False

    async def get_state(self, key: str) -> str | None:
        """bot_state 테이블에서 key 조회"""
        try:
            async with self.db.execute(
                "SELECT value FROM bot_state WHERE key = ?", (key,)
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error(f"get_state 오류 [{key}]: {e}")
            return None

    async def delete_state(self, key: str) -> bool:
        """bot_state 테이블에서 key 삭제"""
        try:
            await self.db.execute("DELETE FROM bot_state WHERE key = ?", (key,))
            await self.db.commit()
            return True
        except Exception as e:
            logger.error(f"delete_state 오류 [{key}]: {e}")
            return False
    async def close(self):
        """DB 연결 종료"""
        if self._conn:
            await self._conn.close()
            logger.info("DB 연결 종료")

