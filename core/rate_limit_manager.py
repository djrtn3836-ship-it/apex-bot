"""APEX BOT - Upbit API Rate Limit Manager
Upbit :  10 (ORDER),  30 (QUERY)"""
import time
import asyncio
from collections import deque
from loguru import logger


class RateLimitManager:
    """Upbit API    
    - ORDER  :   10
    - QUERY  :   30
    -     (sleep)"""

    ORDER_LIMIT  = 8   # 초당 8회 (안전 마진 20%)
    QUERY_LIMIT  = 25  # 초당 25회 (안전 마진 17%)
    WINDOW_SEC   = 1.0

    def __init__(self):
        self._order_times: deque = deque()
        self._query_times: deque = deque()
        logger.info(" RateLimitManager  | ORDER=8/s | QUERY=25/s")

    def _cleanup(self, dq: deque, now: float):
        """1"""
        while dq and now - dq[0] > self.WINDOW_SEC:
            dq.popleft()

    def _wait_sync(self, dq: deque, limit: int, label: str):
        """_wait_sync 실행"""
        while True:
            now = time.time()
            self._cleanup(dq, now)
            if len(dq) < limit:
                dq.append(now)
                return
            sleep_ms = self.WINDOW_SEC - (now - dq[0])
            if sleep_ms > 0:
                logger.debug(f" RateLimit  ({label}): {sleep_ms*1000:.0f}ms")
                time.sleep(sleep_ms)

    async def _wait_async(self, dq: deque, limit: int, label: str):
        """_wait_async 실행"""
        while True:
            now = time.time()
            self._cleanup(dq, now)
            if len(dq) < limit:
                dq.append(now)
                return
            sleep_ms = self.WINDOW_SEC - (now - dq[0])
            if sleep_ms > 0:
                logger.debug(f" RateLimit  ({label}): {sleep_ms*1000:.0f}ms")
                await asyncio.sleep(sleep_ms)

    def acquire_order(self):
        """API"""
        self._wait_sync(self._order_times, self.ORDER_LIMIT, "ORDER")

    def acquire_query(self):
        """API"""
        self._wait_sync(self._query_times, self.QUERY_LIMIT, "QUERY")

    async def async_acquire_order(self):
        """API"""
        await self._wait_async(self._order_times, self.ORDER_LIMIT, "ORDER")

    async def async_acquire_query(self):
        """API"""
        await self._wait_async(self._query_times, self.QUERY_LIMIT, "QUERY")

    def get_status(self) -> dict:
        now = time.time()
        self._cleanup(self._order_times, now)
        self._cleanup(self._query_times, now)
        return {
            "order_count":  len(self._order_times),
            "query_count":  len(self._query_times),
            "order_limit":  self.ORDER_LIMIT,
            "query_limit":  self.QUERY_LIMIT,
        }
