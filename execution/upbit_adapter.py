"""APEX BOT -  API 
pyupbit  +   +   +"""
import asyncio
import time
from typing import Optional, Dict, List, Any
from loguru import logger

try:
    import pyupbit
except ImportError:
    pyupbit = None
    logger.warning("pyupbit  -   ")

from config.settings import get_settings
from utils.helpers import async_retry, RateLimiter, round_price


class UpbitAdapter:
    """REST API + WebSocket  
    -    (REST 10req/s,  8req/s)
    -   
    -"""

    def __init__(self):
        self.settings = get_settings()
        self.is_paper = (self.settings.mode != "live")
        self._upbit = None
        self._rest_limiter = RateLimiter(calls_per_second=8)
        self._order_limiter = RateLimiter(calls_per_second=6)
        self._paper_balance: Dict[str, float] = {}
        self._paper_orders: List[Dict] = []
        self._order_counter = 0

    # ── 초기화 ───────────────────────────────────────────────────
    async def initialize(self):
        if self.is_paper:
            self._paper_balance = {
                "KRW": 1_000_000.0,
                "BTC": 0.0,
                "ETH": 0.0,
            }
            logger.info("     ( : ₩1,000,000)")
        else:
            if pyupbit is None:
                raise RuntimeError("  pyupbit ")
            self._upbit = pyupbit.Upbit(
                self.settings.api.access_key,
                self.settings.api.secret_key,
            )
            balance = await self.get_balance("KRW")
            logger.info(f"  API   | KRW : ₩{balance:,.0f}")

    # ── 잔고 조회 ────────────────────────────────────────────────
    @async_retry(max_attempts=3, delay=0.5)
    async def get_balance(self, currency: str = "KRW") -> float:
        await self._rest_limiter.acquire()
        if self.is_paper:
            return self._paper_balance.get(currency, 0.0)
        try:
            return self._upbit.get_balance(currency) or 0.0
        except Exception as e:
            logger.error(f"   ({currency}): {e}")
            return 0.0

    @async_retry(max_attempts=3, delay=0.5)
    async def get_all_balances(self) -> Dict[str, float]:
        await self._rest_limiter.acquire()
        if self.is_paper:
            return {k: v for k, v in self._paper_balance.items() if v > 0}
        try:
            balances = self._upbit.get_balances()
            return {b["currency"]: float(b["balance"]) for b in balances}
        except Exception as e:
            logger.error(f"   : {e}")
            return {}

    # ── 시세 조회 ────────────────────────────────────────────────
    @async_retry(max_attempts=3, delay=0.5)
    async def get_current_price(self, market: str) -> Optional[float]:
        await self._rest_limiter.acquire()
        if self.is_paper:
            if pyupbit:
                try:
                    price = pyupbit.get_current_price(market)
                    return float(price) if price else None
                except Exception:
                    return None
            return None
        try:
            price = pyupbit.get_current_price(market)
            return float(price) if price else None
        except Exception as e:
            logger.error(f"   ({market}): {e}")
            return None

    @async_retry(max_attempts=3, delay=1.0)
    async def get_ohlcv(
        self,
        market: str,
        interval: str = "minute60",
        count: int = 200,
    ) -> Optional[Any]:
        await self._rest_limiter.acquire()
        if pyupbit is None:
            return None
        try:
            df = pyupbit.get_ohlcv(market, interval=interval, count=count)
            return df
        except Exception as e:
            logger.error(f"OHLCV   ({market}): {e}")
            return None

    # ── 주문 실행 ────────────────────────────────────────────────
    @async_retry(max_attempts=3, delay=1.0)
    async def buy_limit_order(
        self, market: str, price: float, amount_krw: float
    ) -> Optional[Dict]:
        await self._order_limiter.acquire()
        price = round_price(price, market)
        volume = amount_krw / price

        if self.is_paper:
            return await self._paper_buy(market, price, volume, "limit")

        try:
            result = self._upbit.buy_limit_order(market, price, volume)
            if result and "uuid" in result:
                logger.info(
                    f"    | {market} | {price:,} × {volume:.8f}"
                )
                return result
            logger.error(f"  : {result}")
            return None
        except Exception as e:
            logger.error(f"   ({market}): {e}")
            return None

    @async_retry(max_attempts=3, delay=1.0)
    async def buy_market_order(
        self, market: str, amount_krw: float
    ) -> Optional[Dict]:
        await self._order_limiter.acquire()
        price = await self.get_current_price(market)
        if not price:
            return None

        if self.is_paper:
            return await self._paper_buy(
                market, price, amount_krw / price, "market"
            )

        try:
            result = self._upbit.buy_market_order(market, amount_krw)
            if result and "uuid" in result:
                logger.info(f"   | {market} | ₩{amount_krw:,}")
                return result
            return None
        except Exception as e:
            logger.error(f"   ({market}): {e}")
            return None

    @async_retry(max_attempts=3, delay=1.0)
    async def sell_limit_order(
        self, market: str, price: float, volume: float
    ) -> Optional[Dict]:
        await self._order_limiter.acquire()
        price = round_price(price, market)

        if self.is_paper:
            return await self._paper_sell(market, price, volume, "limit")

        try:
            result = self._upbit.sell_limit_order(market, price, volume)
            if result and "uuid" in result:
                logger.info(
                    f"    | {market} | {price:,} × {volume:.8f}"
                )
                return result
            return None
        except Exception as e:
            logger.error(f"   ({market}): {e}")
            return None

    @async_retry(max_attempts=3, delay=1.0)
    async def sell_market_order(
        self, market: str, volume: float
    ) -> Optional[Dict]:
        await self._order_limiter.acquire()
        price = await self.get_current_price(market)
        if not price:
            return None

        if self.is_paper:
            return await self._paper_sell(market, price, volume, "market")

        try:
            result = self._upbit.sell_market_order(market, volume)
            if result and "uuid" in result:
                logger.info(f"   | {market} | {volume:.8f}")
                return result
            return None
        except Exception as e:
            logger.error(f"   ({market}): {e}")
            return None

    # ── 주문 조회 / 취소 ─────────────────────────────────────────
    @async_retry(max_attempts=3, delay=0.5)
    async def get_order(self, order_uuid: str) -> Optional[Dict]:
        await self._rest_limiter.acquire()
        if self.is_paper:
            return next(
                (o for o in self._paper_orders if o["uuid"] == order_uuid),
                None,
            )
        try:
            return self._upbit.get_order(order_uuid)
        except Exception as e:
            logger.error(f"   ({order_uuid}): {e}")
            return None

    @async_retry(max_attempts=3, delay=0.5)
    async def cancel_order(self, order_uuid: str) -> bool:
        await self._order_limiter.acquire()
        if self.is_paper:
            for order in self._paper_orders:
                if order["uuid"] == order_uuid and order["state"] == "wait":
                    order["state"] = "cancelled"
                    return True
            return False
        try:
            result = self._upbit.cancel_order(order_uuid)
            return result is not None
        except Exception as e:
            logger.error(f"   ({order_uuid}): {e}")
            return False

    @async_retry(max_attempts=3, delay=0.5)
    async def get_open_orders(self, market: str = None) -> List[Dict]:
        await self._rest_limiter.acquire()
        if self.is_paper:
            orders = [o for o in self._paper_orders if o["state"] == "wait"]
            if market:
                orders = [o for o in orders if o["market"] == market]
            return orders
        try:
            result = (
                self._upbit.get_order(market, state="wait") if market else []
            )
            return result or []
        except Exception as e:
            logger.error(f"   : {e}")
            return []

    # ── 시장 정보 ────────────────────────────────────────────────
    @async_retry(max_attempts=3, delay=1.0)
    async def get_all_krw_markets(self) -> List[str]:
        await self._rest_limiter.acquire()
        if pyupbit:
            try:
                tickers = pyupbit.get_tickers(fiat="KRW")
                return tickers or []
            except Exception as e:
                logger.error(f"   : {e}")
        return []

    # ── 페이퍼 트레이딩 내부 로직 ────────────────────────────────
    async def _paper_buy(
        self,
        market: str,
        price: float,
        volume: float,
        order_type: str,
    ) -> Dict:
        if price <= 0:
            price = await self.get_current_price(market) or 0
        if price <= 0:
            logger.warning(f"  :    ({market})")
            return {"error": "no_price"}
        if volume <= 0:
            krw_temp = self._paper_balance.get("KRW", 0)
            volume = krw_temp / price * 0.1

        fee = price * volume * self.settings.trading.fee_rate
        total_cost = price * volume + fee
        krw = self._paper_balance.get("KRW", 0)

        logger.info(
            f" [PAPER_BUY] {market} | price={price:,} | "
            f"vol={volume:.6f} | cost={total_cost:,.0f} | KRW={krw:,.0f}"
        )

        if krw < total_cost:
            logger.warning(
                f"  :   "
                f"(: {total_cost:,.0f}, : {krw:,.0f})"
            )
            return {"error": "insufficient_balance"}

        self._paper_balance["KRW"] = krw - total_cost
        coin = market.split("-")[1]
        self._paper_balance[coin] = self._paper_balance.get(coin, 0) + volume

        self._order_counter += 1
        order = {
            "uuid":             f"paper_{self._order_counter}",
            "market":           market,
            "side":             "bid",
            "ord_type":         order_type,
            "price":            str(price),
            "volume":           str(volume),
            "executed_volume":  str(volume),
            "state":            "done",
            "created_at":       time.time(),
            "fee":              fee,
        }
        self._paper_orders.append(order)
        logger.info(
            f" [PAPER]   | {market} | "
            f"{price:,} × {volume:.6f} (: {fee:,.0f}KRW)"
        )
        return order

    async def _paper_sell(
        self,
        market: str,
        price: float,
        volume: float,
        order_type: str,
    ) -> Dict:
        coin = market.split("-")[1]
        held = self._paper_balance.get(coin, 0)

        if held < volume:
            volume = held
        if volume <= 0:
            return {"error": "no_balance"}

        fee = price * volume * self.settings.trading.fee_rate
        proceeds = price * volume - fee

        self._paper_balance["KRW"] = self._paper_balance.get("KRW", 0) + proceeds
        self._paper_balance[coin] = held - volume

        self._order_counter += 1
        order = {
            "uuid":             f"paper_{self._order_counter}",
            "market":           market,
            "side":             "ask",
            "ord_type":         order_type,
            "price":            str(price),
            "volume":           str(volume),
            "executed_volume":  str(volume),
            "state":            "done",
            "created_at":       time.time(),
            "fee":              fee,
        }
        self._paper_orders.append(order)
        logger.info(
            f" [PAPER]   | {market} | "
            f"{price:,} × {volume:.6f} (: {fee:,.0f}KRW)"
        )
        return order

    # ── 상태 정보 ────────────────────────────────────────────────
    async def get_balances(self) -> List[Dict]:
        """SmartWalletManager   .
         FIX:     self.positions  .
                _paper_balance   .

         :
            [{"currency": "ETH", "balance": "0.001",
              "avg_buy_price": "0", "current_price": 0}]"""
        try:
            if self.is_paper:
                # ✅ FIX: _paper_balance에서 코인 잔고만 추출 (KRW 제외)
                result = []
                for currency, balance in self._paper_balance.items():
                    if currency == "KRW":
                        continue
                    if balance > 1e-10:
                        result.append({
                            "currency":      currency,
                            "balance":       str(balance),
                            "avg_buy_price": "0",
                            "current_price": 0,
                        })
                return result
            else:
                # 실거래 모드: pyupbit API
                if not self._upbit:
                    return []
                raw = self._upbit.get_balances()
                if not raw or not isinstance(raw, list):
                    return []
                return [
                    {
                        "currency":      b.get("currency", ""),
                        "balance":       str(b.get("balance", 0)),
                        "avg_buy_price": str(b.get("avg_buy_price", 0)),
                        "current_price": 0,
                    }
                    for b in raw
                    if b.get("currency") not in ("KRW", "")
                ]
        except Exception as e:
            logger.warning(f"get_balances : {e}")
            return []

    def sync_paper_balance(self, krw_balance: float, positions: dict):
        """docstring"""
        if not self.is_paper:
            return
        self._paper_balance["KRW"] = krw_balance
        for market, pos in positions.items():
            coin = market.split("-")[1]
            volume = pos.get("volume", pos.get("qty", 0))
            if volume > 0:
                self._paper_balance[coin] = volume
        logger.info(
            f"     | KRW=₩{krw_balance:,.0f} | "
            f"={len(positions)}종목"
        )

    def get_paper_portfolio_summary(self) -> Dict:
        return {
            "mode":            "PAPER",
            "balance":         self._paper_balance.copy(),
            "total_orders":    len(self._paper_orders),
            "executed_orders": sum(
                1 for o in self._paper_orders if o["state"] == "done"
            ),
        }
