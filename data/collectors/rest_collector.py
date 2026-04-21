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


class UpbitRateLimiter:
    """
    Upbit Remaining-Req 헤더 기반 동적 Rate Limiter
    - 그룹별 독립 관리 (candles / ticker / orderbook / order / default)
    - 서버 응답 헤더로 실시간 잔여량 동기화
    - 잔여량 2 이하 시 자동 대기 (버퍼 보호)
    - 429 수신 시 즉시 해당 그룹 소진 처리
    """

    # Upbit 공식 스펙 (초당 최대 요청)
    GROUP_CAPACITY = {
        "candles":          10,
        "ticker":           10,
        "trades":           10,
        "orderbook":        10,
        "market":           10,
        "order":             8,
        "order-cancel-all":  1,
        "default":          30,
    }
    WINDOW_SEC = 1
    BUFFER = 2  # 잔여량 이 값 이하면 다음 윈도우까지 대기

    def __init__(self):
        # group → {"remaining": int, "win_start": float}
        self._state: Dict[str, Dict] = {}
        self._lock = asyncio.Lock()

    def _now_win(self) -> float:
        t = time.time()
        return t - (t % self.WINDOW_SEC)

    async def acquire(self, group: str = "candles"):
        """요청 전 호출 - 잔여량 확인 후 필요시 대기"""
        async with self._lock:
            cap = self.GROUP_CAPACITY.get(group, 10)
            win = self._now_win()
            state = self._state.get(group, {"remaining": cap, "win_start": win})

            # 윈도우 갱신
            if state["win_start"] != win:
                state = {"remaining": cap, "win_start": win}

            # 잔여량이 BUFFER 이하면 다음 윈도우까지 대기
            if state["remaining"] <= self.BUFFER:
                sleep_for = (state["win_start"] + self.WINDOW_SEC) - time.time() + 0.02
                if sleep_for > 0:
                    logger.debug(
                        f"[RATE] group={group} remaining={state['remaining']} → "
                        f"{sleep_for:.3f}초 대기"
                    )
                    await asyncio.sleep(sleep_for)
                # 새 윈도우로 리셋
                win = self._now_win()
                state = {"remaining": cap, "win_start": win}

            state["remaining"] -= 1
            self._state[group] = state

    def update_from_header(self, header_value: Optional[str]):
        """Remaining-Req 헤더 파싱 → 실시간 잔여량 동기화
        헤더 형식: group=candles; min=600; sec=8
        """
        if not header_value:
            return
        try:
            group, sec = "default", None
            for part in header_value.split(";"):
                part = part.strip()
                if part.startswith("group="):
                    group = part.split("=", 1)[1].strip()
                elif part.startswith("sec="):
                    sec = int(part.split("=", 1)[1].strip())
            if sec is not None and group in self.GROUP_CAPACITY:
                win = self._now_win()
                self._state[group] = {"remaining": sec, "win_start": win}
                logger.debug(f"[RATE] 헤더 동기화: group={group} remaining={sec}")
        except Exception as _e:
            logger.debug(f"[RATE] 헤더 파싱 실패 (무시): {_e}")

    def mark_exhausted(self, group: str = "candles"):
        """429 수신 시 해당 그룹 즉시 소진 처리"""
        win = self._now_win()
        self._state[group] = {"remaining": 0, "win_start": win}
        logger.warning(f"[RATE] 429 수신 → group={group} 소진 처리")


class RestCollector:
    """REST API
    - OHLCV (//)
    -  /  /
    -    ( 10)
    - pyupbit fallback → aiohttp
    - Remaining-Req 헤더 기반 동적 Rate Limiting"""

    BASE_URL = "https://api.upbit.com/v1"
    # 동시 요청 제한 (세마포어 실제 적용)
    MAX_CONCURRENT = 3

    def __init__(self):
        self.settings = get_settings()
        self._limiter   = RateLimiter(calls_per_second=5)   # 기존 호환성 유지
        self._rl        = UpbitRateLimiter()                 # 신규 동적 리미터
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)  # 실제 동시 제한
        self._session   = None
        self._cache: Dict[str, Dict] = {}

    async def get_ohlcv(
        self, market: str, interval: str = "minute60",
        count: int = 200
    ) -> Optional[pd.DataFrame]:
        """OHLCV - Remaining-Req 기반 동적 Rate Limiting 적용"""

        cache_key = f"{market}_{interval}_{count}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if time.time() - cached["ts"] < 60:
                return cached["data"]

        # 동적 Rate Limiter 획득 (세마포어 내부에서)
        async with self._semaphore:
            await self._rl.acquire("candles")

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

            return await self._fetch_ohlcv_api(market, interval, count)

    @async_retry(max_attempts=3, delay=1.0)
    async def _fetch_ohlcv_api(self, market: str, interval: str, count: int) -> Optional[pd.DataFrame]:
        """aiohttp OHLCV - Remaining-Req 헤더 파싱 적용"""
        try:
            import aiohttp
            endpoint, params = self._build_candle_request(market, interval, count)

            async with aiohttp.ClientSession() as session:
                async with session.get(endpoint, params=params) as resp:
                    # Remaining-Req 헤더 즉시 동기화
                    self._rl.update_from_header(resp.headers.get("Remaining-Req"))

                    if resp.status == 429:
                        self._rl.mark_exhausted("candles")
                        logger.warning(f"[429] {market} → 1초 대기 후 재시도")
                        await asyncio.sleep(1.0)
                        return None
                    if resp.status == 401:
                        logger.critical(
                            f"[API-401] API 키 인증 실패 ({market}) - 실거래 위험!"
                        )
                        return None
                    if resp.status != 200:
                        logger.error(f"API 오류 ({resp.status}): {market}")
                        return None

                    data = await resp.json()

            if not data:
                return None

            df = pd.DataFrame(data)
            df = df.rename(columns={
                "candle_date_time_kst": "datetime",
                "opening_price":        "open",
                "high_price":           "high",
                "low_price":            "low",
                "trade_price":          "close",
                "candle_acc_trade_volume": "volume",
            })
            df["datetime"] = pd.to_datetime(df["datetime"])
            df = df.set_index("datetime").sort_index()
            df = df[["open", "high", "low", "close", "volume"]].astype(float)
            self._cache[f"{market}_minute60_200"] = {"ts": time.time(), "data": df}
            return df

        except Exception as e:
            logger.error(f"OHLCV API 오류 ({market}): {e}")
            return None

    def _build_candle_request(self, market: str, interval: str, count: int):
        """캔들 API 엔드포인트 생성"""
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
        """현재가 조회 - Remaining-Req 기반 동적 Rate Limiting 적용"""
        async with self._semaphore:
            await self._rl.acquire("ticker")
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
        """ticker aiohttp 직접 호출 - Remaining-Req 헤더 파싱"""
        try:
            import aiohttp
            market_str = ",".join(markets)
            url = f"{self.BASE_URL}/ticker"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params={"markets": market_str}) as resp:
                    self._rl.update_from_header(resp.headers.get("Remaining-Req"))
                    if resp.status == 429:
                        self._rl.mark_exhausted("ticker")
                        await asyncio.sleep(1.0)
                        return None
                    if resp.status == 200:
                        return await resp.json()
        except Exception as e:
            logger.error(f"ticker 오류: {e}")
        return None

    async def get_orderbook(self, market: str) -> Optional[Dict]:
        """호가창 조회 - Remaining-Req 기반 동적 Rate Limiting 적용"""
        async with self._semaphore:
            await self._rl.acquire("orderbook")
            try:
                import aiohttp
                url = f"{self.BASE_URL}/orderbook"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, params={"markets": market}) as resp:
                        self._rl.update_from_header(resp.headers.get("Remaining-Req"))
                        if resp.status == 429:
                            self._rl.mark_exhausted("orderbook")
                            await asyncio.sleep(1.0)
                            return None
                        if resp.status == 200:
                            data = await resp.json()
                            return data[0] if data else None
            except Exception as e:
                logger.error(f"orderbook 오류 ({market}): {e}")
        return None

    async def get_multiple_ohlcv(
        self, markets: List[str], interval: str = "minute60", count: int = 200
    ) -> Dict[str, pd.DataFrame]:
        """
        다중 OHLCV 수집 - 순차 배치 처리 (burst 완전 방지)
        MAX_CONCURRENT개씩 묶어서 처리 → Semaphore + RateLimiter 완전 적용
        """
        results = {}
        # MAX_CONCURRENT개씩 배치로 순차 처리
        batch_size = self.MAX_CONCURRENT
        for i in range(0, len(markets), batch_size):
            batch = markets[i:i + batch_size]
            tasks = [self.get_ohlcv(m, interval, count) for m in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for market, df in zip(batch, batch_results):
                if df is not None and not isinstance(df, Exception):
                    results[market] = df
            # 배치 간 최소 간격 (burst 방지)
            if i + batch_size < len(markets):
                await asyncio.sleep(0.3)
        return results

    @staticmethod
    def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
        """pyupbit DataFrame 정규화"""
        df.index = pd.to_datetime(df.index)
        df.index.name = "datetime"
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = df[col].astype(float)
        return df.sort_index()


# alias
UpbitRestCollector = RestCollector
