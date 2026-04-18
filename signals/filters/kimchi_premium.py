"""APEX BOT -   
(KRW) vs (USDT)   →  
    ,    

: USD/KRW   API"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional, Tuple
from loguru import logger

try:
    import aiohttp
    AIOHTTP_OK = True
except ImportError:
    AIOHTTP_OK = False


# 환율 fallback (실시간 조회 실패 시 사용)
FALLBACK_USDKRW = 1350.0

# 마켓 매핑 (업비트 → 바이낸스)
UPBIT_TO_BINANCE: Dict[str, str] = {
    "KRW-BTC":  "BTCUSDT",
    "KRW-ETH":  "ETHUSDT",
    "KRW-XRP":  "XRPUSDT",
    "KRW-SOL":  "SOLUSDT",
    "KRW-ADA":  "ADAUSDT",
    "KRW-DOGE": "DOGEUSDT",
    "KRW-AVAX": "AVAXUSDT",
    "KRW-DOT":  "DOTUSDT",
    "KRW-LINK": "LINKUSDT",
    "KRW-ATOM": "ATOMUSDT",
}


class KimchiPremiumMonitor:
    """:
      +3% ~ +7% :   (  )
      +7%   :  →   
      +10%  :  →   
      0%    :  →  

     : 5"""

    # 프리미엄 임계값
    PREMIUM_CAUTION  = 0.07   # 7% → 매수 주의 (신뢰도 감소)
    PREMIUM_BLOCK    = 0.10   # 10% → 매수 차단
    DISCOUNT_BOOST   = 0.00   # 0% 이하 → 매수 강화
    CACHE_TTL        = 300    # 5분 캐시

    def __init__(self, usd_krw_rate: float = FALLBACK_USDKRW):
        self._usd_krw = usd_krw_rate
        self._upbit_prices: Dict[str, float] = {}
        self._binance_prices: Dict[str, float] = {}
        self._premium_cache: Dict[str, Tuple[float, float]] = {}  # market → (premium, timestamp)
        self._last_update: float = 0
        self._available = AIOHTTP_OK

        if not AIOHTTP_OK:
            logger.warning(" aiohttp  →   ")
        else:
            logger.info("   ")

    # ── Public API ──────────────────────────────────────────────────

    def update_upbit_price(self, market: str, price: float):
        """(WebSocket )"""
        self._upbit_prices[market] = price

    def update_upbit_prices(self, price_map: Dict[str, float]):
        """update_upbit_prices 실행"""
        self._upbit_prices.update(price_map)

    def get_premium(self, market: str) -> Optional[float]:
        """Returns:
              (0.05 = 5%)  None ( )"""
        cache = self._premium_cache.get(market)
        if cache:
            prem, ts = cache
            if time.time() - ts < self.CACHE_TTL:
                return prem

        upbit = self._upbit_prices.get(market)
        binance_symbol = UPBIT_TO_BINANCE.get(market)
        if not binance_symbol:
            return None

        binance = self._binance_prices.get(binance_symbol)
        if not upbit or not binance or binance <= 0 or self._usd_krw <= 0:
            return None

        # 김치 프리미엄 = (업비트 KRW가 / (바이낸스 USDT × 환율)) - 1
        fair_krw = binance * self._usd_krw
        premium = (upbit - fair_krw) / fair_krw

        self._premium_cache[market] = (premium, time.time())
        return premium

    def can_buy(self, market: str) -> Tuple[bool, str, float]:
        """Returns:
            ( , ,   -1.0 ~ +0.2)"""
        premium = self.get_premium(market)
        if premium is None:
            return True, "프리미엄 데이터 없음 (통과)", 0.0

        pct = premium * 100

        if premium >= self.PREMIUM_BLOCK:
            return (
                False,
                f"김치 프리미엄 과열 {pct:.1f}% ≥ {self.PREMIUM_BLOCK*100:.0f}% → 매수 차단",
                -1.0,
            )

        if premium >= self.PREMIUM_CAUTION:
            confidence_adj = -(premium - self.PREMIUM_CAUTION) * 5  # 최대 -0.15
            return (
                True,
                f"김치 프리미엄 주의 {pct:.1f}% → 신뢰도 하향",
                confidence_adj,
            )

        if premium <= self.DISCOUNT_BOOST:
            confidence_adj = min(abs(premium) * 2, 0.10)  # 최대 +0.10
            return (
                True,
                f"김치 디스카운트 {pct:.1f}% → 매수 유리",
                confidence_adj,
            )

        # 정상 범위 (0% ~ 7%)
        return True, f"김치 프리미엄 정상 {pct:.1f}%", 0.0

    def get_all_premiums(self) -> Dict[str, Optional[float]]:
        """get_all_premiums 실행"""
        return {market: self.get_premium(market) for market in UPBIT_TO_BINANCE}

    def get_summary(self) -> str:
        """get_summary 실행"""
        lines = ["📊 김치 프리미엄 현황:"]
        for market in list(UPBIT_TO_BINANCE.keys())[:5]:
            prem = self.get_premium(market)
            coin = market.replace("KRW-", "")
            if prem is not None:
                icon = "🔴" if prem >= self.PREMIUM_BLOCK else (
                    "🟡" if prem >= self.PREMIUM_CAUTION else "🟢"
                )
                lines.append(f"  {icon} {coin}: {prem*100:+.2f}%")
            else:
                lines.append(f"  ⚪ {coin}: N/A")
        return "\n".join(lines)

    # ── Async Fetchers ──────────────────────────────────────────────

    async def fetch_all(self):
        """+    (5   )"""
        if not self._available:
            return

        try:
            await asyncio.gather(
                self._fetch_binance_prices(),
                self._fetch_usd_krw(),
                return_exceptions=True,
            )
            self._last_update = time.time()
            self._premium_cache.clear()  # 캐시 갱신

        except Exception as e:
            logger.warning(f"  : {e}")

    async def _fetch_binance_prices(self):
        """_fetch_binance_prices 실행"""
        if not AIOHTTP_OK:
            return

        symbols = list(UPBIT_TO_BINANCE.values())
        symbols_str = str(symbols).replace("'", '"').replace(" ", "")
        url = f"https://api.binance.com/api/v3/ticker/price?symbols={symbols_str}"

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for item in data:
                            self._binance_prices[item["symbol"]] = float(item["price"])
                        logger.debug(
                            f"  : {len(self._binance_prices)}개"
                        )
        except Exception as e:
            logger.debug(f"  : {e}")

    async def _fetch_usd_krw(self):
        """USD/KRW   (Open Exchange Rates  API)"""
        if not AIOHTTP_OK:
            return

        # 무료 환율 API (등록 불필요)
        urls = [
            "https://open.er-api.com/v6/latest/USD",
            "https://api.exchangerate-api.com/v4/latest/USD",
        ]

        for url in urls:
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=8)
                ) as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            krw = (
                                data.get("rates", {}).get("KRW")
                                or data.get("conversion_rates", {}).get("KRW")
                            )
                            if krw:
                                self._usd_krw = float(krw)
                                logger.debug(f" : ₩{self._usd_krw:,.0f}/USD")
                                return
            except Exception:
                continue

        logger.debug(f"   → fallback ₩{FALLBACK_USDKRW:,}/USD ")
