# data/processors/mtf_processor.py — 다중 타임프레임 데이터 수집/관리
"""6  OHLCV  
  1m  → / 
  5m  →   
  15m →  
  60m →   ()
  4h  →  
  1d  →   (EMA200  )"""

import asyncio
from typing import Dict, Optional
import pandas as pd
from data.collectors.rest_collector import RestCollector
from data.processors.candle_processor import CandleProcessor
from utils.logger import logger


TF_CONFIG = {
    "1m":  {"unit": "minutes", "count": 200, "interval": "1"},
    "5m":  {"unit": "minutes", "count": 200, "interval": "5"},
    "15m": {"unit": "minutes", "count": 200, "interval": "15"},
    "60m": {"unit": "minutes", "count": 200, "interval": "60"},
    "4h":  {"unit": "minutes", "count": 100, "interval": "240"},
    "1d":  {"unit": "days",    "count": 200, "interval": "1"},
}


class MTFProcessor:
    """MTFProcessor 클래스"""
    REFRESH_SECONDS = {
        "1m": 60, "5m": 300, "15m": 900,
        "60m": 3600, "4h": 14400, "1d": 86400,
    }

    def __init__(self):
        self.collector  = RestCollector()
        self.processor  = CandleProcessor()
        self._cache: Dict[str, Dict[str, pd.DataFrame]] = {}
        self._last_update: Dict[str, Dict[str, float]] = {}

    async def get(
        self, market: str, timeframe: str, force_refresh: bool = False
    ) -> Optional[pd.DataFrame]:
        """DataFrame  (  )"""
        import time
        now = time.time()
        ttl = self.REFRESH_SECONDS.get(timeframe, 60)

        last = self._last_update.get(market, {}).get(timeframe, 0)
        if force_refresh or (now - last) > ttl:
            await self._fetch(market, timeframe)

        return self._cache.get(market, {}).get(timeframe)

    async def get_all(self, market: str) -> Dict[str, Optional[pd.DataFrame]]:
        """6"""
        tasks = [self.get(market, tf) for tf in TF_CONFIG]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            tf: (r if not isinstance(r, Exception) else None)
            for tf, r in zip(TF_CONFIG.keys(), results)
        }

    async def _fetch(self, market: str, timeframe: str):
        """REST API OHLCV  +"""
        import time
        cfg = TF_CONFIG[timeframe]
        try:
            df_raw = await self.collector.get_ohlcv(
                market,
                f"minute{cfg['interval']}" if cfg["unit"] == "minutes" else "day",
                cfg["count"],
            )
            df = await self.processor.process(market, df_raw, cfg["interval"])

            if market not in self._cache:
                self._cache[market] = {}
                self._last_update[market] = {}

            self._cache[market][timeframe] = df
            self._last_update[market][timeframe] = time.time()

        except Exception as e:
            logger.warning(f"[MTF] {market} {timeframe}  : {e}")
