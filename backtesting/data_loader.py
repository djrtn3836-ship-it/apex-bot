"""
APEX BOT Backtester - Upbit 과거 데이터 로더
Upbit REST API를 통해 OHLCV 캔들 데이터를 수집합니다.
"""
import time
import asyncio
import aiohttp
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional
from loguru import logger


UPBIT_BASE = "https://api.upbit.com/v1/candles"

INTERVAL_MAP = {
    "1m":   ("minutes/1",   1),
    "3m":   ("minutes/3",   3),
    "5m":   ("minutes/5",   5),
    "15m":  ("minutes/15",  15),
    "30m":  ("minutes/30",  30),
    "1h":   ("minutes/60",  60),
    "4h":   ("minutes/240", 240),
    "1d":   ("days",        1440),
    "1w":   ("weeks",       10080),
}


async def _fetch_chunk(
    session: aiohttp.ClientSession,
    market: str,
    interval: str,
    to: Optional[str] = None,
    count: int = 200,
) -> list:
    """Upbit API에서 캔들 200개씩 가져오기"""
    path, _ = INTERVAL_MAP[interval]
    url = f"{UPBIT_BASE}/{path}"
    params = {"market": market, "count": count}
    if to:
        params["to"] = to

    headers = {"Accept": "application/json"}
    try:
        async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 429:
                logger.warning("Rate limit hit, sleeping 1s...")
                await asyncio.sleep(1)
                return []
            if resp.status != 200:
                logger.error(f"HTTP {resp.status} for {market} {interval}")
                return []
            data = await resp.json()
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"Fetch error {market} {interval}: {e}")
        return []


async def fetch_ohlcv(
    market: str,
    interval: str = "1d",
    days: int = 365,
) -> pd.DataFrame:
    """
    Upbit에서 OHLCV 데이터를 수집하여 DataFrame으로 반환합니다.

    Args:
        market:   예) "KRW-BTC", "KRW-ETH"
        interval: "1m","5m","15m","30m","1h","4h","1d","1w"
        days:     과거 며칠치 데이터 (일봉 기준)

    Returns:
        pd.DataFrame with columns: open, high, low, close, volume
        index: datetime (KST)
    """
    if interval not in INTERVAL_MAP:
        raise ValueError(f"지원하지 않는 interval: {interval}. 가능: {list(INTERVAL_MAP)}")

    _, minutes_per_bar = INTERVAL_MAP[interval]
    total_bars = max(1, (days * 1440) // minutes_per_bar)
    logger.info(f"[DataLoader] {market} {interval} {total_bars}봉 수집 시작...")

    all_rows = []
    to_str = None

    async with aiohttp.ClientSession() as session:
        while len(all_rows) < total_bars:
            need = min(200, total_bars - len(all_rows))
            chunk = await _fetch_chunk(session, market, interval, to=to_str, count=need)
            if not chunk:
                break
            all_rows.extend(chunk)
            # 다음 페이지: 가장 오래된 캔들 시각 기준
            oldest = chunk[-1].get("candle_date_time_kst") or chunk[-1].get("candle_date_time_utc")
            to_str = oldest
            await asyncio.sleep(0.12)   # 업비트 rate limit 대응

    if not all_rows:
        logger.warning(f"[DataLoader] {market} {interval} 데이터 없음")
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)

    # 컬럼 통일
    rename = {
        "candle_date_time_kst": "datetime",
        "opening_price":        "open",
        "high_price":           "high",
        "low_price":            "low",
        "trade_price":          "close",
        "candle_acc_trade_volume": "volume",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    needed = ["datetime", "open", "high", "low", "close", "volume"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        logger.error(f"컬럼 누락: {missing}")
        return pd.DataFrame()

    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df[~df.index.duplicated(keep="first")]

    logger.info(f"[DataLoader] {market} {interval} {len(df)}봉 로드 완료 ({df.index[0]} ~ {df.index[-1]})")
    return df


def fetch_ohlcv_sync(market: str, interval: str = "1d", days: int = 365) -> pd.DataFrame:
    """동기 버전 (주피터/스크립트에서 편리하게 사용)"""
    return asyncio.run(fetch_ohlcv(market, interval, days))
