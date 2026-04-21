"""APEX BOT - REST API  
 REST API → OHLCV +  +  
   +"""
import asyncio
import time
from typing import Optional, Dict, List
import pandas as pd
import numpy as np
from loguru import logger

try:
    import pyupbit
    PYUPBIT_OK = True
except ImportError:
    PYUPBIT_OK = False

from config.settings import get_settings
from utils.helpers import RateLimiter, async_retry


class RestCollector:
    """REST API  
    - OHLCV (//)
    -  /  / 
    -    ( 10)
    - pyupbit fallback → aiohttp"""

    BASE_URL = "https://api.upbit.com/v1"

    def __init__(self):
        self.settings = get_settings()
        self._limiter   = RateLimiter(calls_per_second=5)
        self._semaphore = asyncio.Semaphore(3)   # 동시 요청 최대 3개
        self._session   = None
        self._cache: Dict[str, Dict] = {}  # 간단한 메모리 캐시

    async def get_ohlcv(
        self, market: str, interval: str = "minute60",
        count: int = 200
    ) -> Optional[pd.DataFrame]:
        """OHLCV   

        Args:
            market: 'KRW-BTC'
            interval: 'minute1'|'minute5'|'minute15'|'minute60'|'minute240'|'day'|'week'
            count:  200 ( )"""
        async with self._semaphore:
            pass  # 동시 요청 제한
        await self._limiter.acquire()

        cache_key = f"{market}_{interval}_{count}"
        # 1분 캐시
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if time.time() - cached["ts"] < 60:
                return cached["data"]

        if PYUPBIT_OK:
            try:
                df = await asyncio.get_event_loop().run_in_executor(
                    None, pyupbit.get_ohlcv, market, interval, count
                )
                if df is not None and not df.empty:
                    df = self._normalize_df(df)
                    self._cache[cache_key] = {"ts": time.time(), "data": df}
                    return df
            except Exception as e:
                logger.warning(f"pyupbit OHLCV  ({market}): {e}")

        # aiohttp 직접 호출 (fallback)
        return await self._fetch_ohlcv_api(market, interval, count)

    @async_retry(max_attempts=3, delay=1.0)
    async def _fetch_ohlcv_api(self, market: str, interval: str, count: int) -> Optional[pd.DataFrame]:
        """aiohttp  OHLCV"""
        try:
            import aiohttp
            endpoint, params = self._build_candle_request(market, interval, count)

            async with aiohttp.ClientSession() as session:
                async with session.get(endpoint, params=params) as resp:
                    if resp.status == 429:
                        # Rate limit → 1초 대기 후 재시도
                        logger.warning(f"API 429 Rate Limit ({market}) → 1초 대기")
                        await asyncio.sleep(1.0)
                        return None
                    if resp.status == 401:
                        logger.critical(f"[API-401] API 키 인증 실패 ({market}) - 실거래 위험! 키 확인 필요")
                        return None
                    if resp.status != 200:
                        logger.error(f"API  ({resp.status}): {market}")
                        return None
                    data = await resp.json()

            if not data:
                return None

            df = pd.DataFrame(data)
            df = df.rename(columns={
                "candle_date_time_kst": "datetime",
                "opening_price": "open",
                "high_price": "high",
                "low_price": "low",
                "trade_price": "close",
                "candle_acc_trade_volume": "volume",
            })
            df["datetime"] = pd.to_datetime(df["datetime"])
            df = df.set_index("datetime").sort_index()
            df = df[["open", "high", "low", "close", "volume"]].astype(float)
            return df

        except Exception as e:
            logger.error(f"OHLCV API    ({market}): {e}")
            return None

    def _build_candle_request(self, market: str, interval: str, count: int):
        """API"""
        params = {"market": market, "count": min(count, 200)}
        if interval.startswith("minute"):
            unit = interval.replace("minute", "")
            endpoint = f"{self.BASE_URL}/candles/minutes/{unit}"
        elif interval == "day":
            endpoint = f"{self.BASE_URL}/candles/days"
        elif interval == "week":
            endpoint = f"{self.BASE_URL}/candles/weeks"
        else:
            endpoint = f"{self.BASE_URL}/candles/minutes/60"
        return endpoint, params

    async def get_ticker(self, markets: List[str]) -> Optional[List[Dict]]:
        """( )"""
        async with self._semaphore:
            pass  # 동시 요청 제한
        await self._limiter.acquire()
        if PYUPBIT_OK:
            try:
                market_str = ",".join(markets)
                result = await asyncio.get_event_loop().run_in_executor(
                    None, pyupbit.get_tickers_krw_ticker, market_str
                )
                return result
            except Exception:
                pass
        return await self._fetch_ticker_api(markets)

    async def _fetch_ticker_api(self, markets: List[str]) -> Optional[List[Dict]]:
        try:
            import aiohttp
            market_str = ",".join(markets)
            url = f"{self.BASE_URL}/ticker"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params={"markets": market_str}) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception as e:
            logger.error(f"  : {e}")
        return None

    async def get_orderbook(self, market: str) -> Optional[Dict]:
        """get_orderbook 실행"""
        async with self._semaphore:
            pass  # 동시 요청 제한
        await self._limiter.acquire()
        try:
            import aiohttp
            url = f"{self.BASE_URL}/orderbook"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params={"markets": market}) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data[0] if data else None
        except Exception as e:
            logger.error(f"   ({market}): {e}")
        return None

    async def get_multiple_ohlcv(
        self, markets: List[str], interval: str = "minute60", count: int = 200
    ) -> Dict[str, pd.DataFrame]:
        """OHLCV"""
        tasks = [self.get_ohlcv(m, interval, count) for m in markets]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            market: df
            for market, df in zip(markets, results)
            if df is not None and not isinstance(df, Exception)
        }

    @staticmethod
    def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
        """pyupbit DataFrame"""
        df.index = pd.to_datetime(df.index)
        df.index.name = "datetime"
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = df[col].astype(float)
        return df.sort_index()

# alias
UpbitRestCollector = RestCollector