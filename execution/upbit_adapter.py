"""
APEX BOT - 업비트 API 어댑터
pyupbit 래핑 + 비동기 지원 + 레이트 리밋 + 자동 재시도
"""
import asyncio
import time
from typing import Optional, Dict, List, Any
from loguru import logger

try:
    import pyupbit
except ImportError:
    pyupbit = None
    logger.warning("pyupbit 미설치 - 모의거래 모드로만 작동")

from config.settings import get_settings
from utils.helpers import async_retry, RateLimiter, round_price


class UpbitAdapter:
    """
    업비트 REST API + WebSocket 통합 어댑터
    - 자동 레이트 리밋 (REST 10req/s, 주문 8req/s)
    - 지수 백오프 재시도
    - 페이퍼 트레이딩 모드 지원
    """

    def __init__(self):
        self.settings = get_settings()
        self.is_paper = (self.settings.mode != "live")
        self._upbit = None
        self._rest_limiter = RateLimiter(calls_per_second=8)  # 안전 마진
        self._order_limiter = RateLimiter(calls_per_second=6)
        self._paper_balance: Dict[str, float] = {}
        self._paper_orders: List[Dict] = []
        self._order_counter = 0

    # ── 초기화 ────────────────────────────────────────────────────
    async def initialize(self):
        """API 클라이언트 초기화"""
        if self.is_paper:
            # 페이퍼 트레이딩: 초기 자본 설정
            self._paper_balance = {
                "KRW": 1_000_000.0,  # 100만원 시작
                "BTC": 0.0,
                "ETH": 0.0,
            }
            logger.info("📝 페이퍼 트레이딩 모드 시작 (초기 자본: ₩1,000,000)")
        else:
            if pyupbit is None:
                raise RuntimeError("실거래 모드에서는 pyupbit가 필요합니다")
            self._upbit = pyupbit.Upbit(
                self.settings.api.access_key,
                self.settings.api.secret_key,
            )
            # API 키 유효성 확인
            balance = await self.get_balance("KRW")
            logger.info(f"✅ 업비트 API 연결 성공 | KRW 잔고: ₩{balance:,.0f}")

    # ── 잔고 조회 ─────────────────────────────────────────────────
    @async_retry(max_attempts=3, delay=0.5)
    async def get_balance(self, currency: str = "KRW") -> float:
        """잔고 조회"""
        await self._rest_limiter.acquire()
        if self.is_paper:
            return self._paper_balance.get(currency, 0.0)
        try:
            return self._upbit.get_balance(currency) or 0.0
        except Exception as e:
            logger.error(f"잔고 조회 실패 ({currency}): {e}")
            return 0.0

    @async_retry(max_attempts=3, delay=0.5)
    async def get_all_balances(self) -> Dict[str, float]:
        """전체 잔고 조회"""
        await self._rest_limiter.acquire()
        if self.is_paper:
            return {k: v for k, v in self._paper_balance.items() if v > 0}
        try:
            balances = self._upbit.get_balances()
            return {b["currency"]: float(b["balance"]) for b in balances}
        except Exception as e:
            logger.error(f"전체 잔고 조회 실패: {e}")
            return {}

    # ── 시세 조회 ─────────────────────────────────────────────────
    @async_retry(max_attempts=3, delay=0.5)
    async def get_current_price(self, market: str) -> Optional[float]:
        """현재가 조회"""
        await self._rest_limiter.acquire()
        if self.is_paper:
            # 페이퍼 모드: pyupbit 공개 API 사용
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
            logger.error(f"현재가 조회 실패 ({market}): {e}")
            return None

    @async_retry(max_attempts=3, delay=1.0)
    async def get_ohlcv(self, market: str, interval: str = "minute60",
                        count: int = 200) -> Optional[Any]:
        """OHLCV 캔들 데이터 조회"""
        await self._rest_limiter.acquire()
        if pyupbit is None:
            return None
        try:
            df = pyupbit.get_ohlcv(market, interval=interval, count=count)
            return df
        except Exception as e:
            logger.error(f"OHLCV 조회 실패 ({market}): {e}")
            return None

    # ── 주문 실행 ─────────────────────────────────────────────────
    @async_retry(max_attempts=3, delay=1.0)
    async def buy_limit_order(self, market: str, price: float,
                              amount_krw: float) -> Optional[Dict]:
        """지정가 매수 주문"""
        await self._order_limiter.acquire()
        price = round_price(price, market)
        volume = amount_krw / price

        if self.is_paper:
            return await self._paper_buy(market, price, volume, "limit")

        try:
            result = self._upbit.buy_limit_order(market, price, volume)
            if result and "uuid" in result:
                logger.info(f"🟢 매수 주문 접수 | {market} | {price:,} × {volume:.8f}")
                return result
            logger.error(f"매수 주문 실패: {result}")
            return None
        except Exception as e:
            logger.error(f"매수 주문 예외 ({market}): {e}")
            return None

    @async_retry(max_attempts=3, delay=1.0)
    async def buy_market_order(self, market: str, amount_krw: float) -> Optional[Dict]:
        """시장가 매수"""
        await self._order_limiter.acquire()
        price = await self.get_current_price(market)
        if not price:
            return None

        if self.is_paper:
            return await self._paper_buy(market, price, amount_krw / price, "market")

        try:
            result = self._upbit.buy_market_order(market, amount_krw)
            if result and "uuid" in result:
                logger.info(f"🟢 시장가 매수 | {market} | ₩{amount_krw:,}")
                return result
            return None
        except Exception as e:
            logger.error(f"시장가 매수 예외 ({market}): {e}")
            return None

    @async_retry(max_attempts=3, delay=1.0)
    async def sell_limit_order(self, market: str, price: float,
                               volume: float) -> Optional[Dict]:
        """지정가 매도"""
        await self._order_limiter.acquire()
        price = round_price(price, market)

        if self.is_paper:
            return await self._paper_sell(market, price, volume, "limit")

        try:
            result = self._upbit.sell_limit_order(market, price, volume)
            if result and "uuid" in result:
                logger.info(f"🔴 매도 주문 접수 | {market} | {price:,} × {volume:.8f}")
                return result
            return None
        except Exception as e:
            logger.error(f"매도 주문 예외 ({market}): {e}")
            return None

    @async_retry(max_attempts=3, delay=1.0)
    async def sell_market_order(self, market: str, volume: float) -> Optional[Dict]:
        """시장가 매도"""
        await self._order_limiter.acquire()
        price = await self.get_current_price(market)
        if not price:
            return None

        if self.is_paper:
            return await self._paper_sell(market, price, volume, "market")

        try:
            result = self._upbit.sell_market_order(market, volume)
            if result and "uuid" in result:
                logger.info(f"🔴 시장가 매도 | {market} | {volume:.8f}")
                return result
            return None
        except Exception as e:
            logger.error(f"시장가 매도 예외 ({market}): {e}")
            return None

    # ── 주문 조회 / 취소 ──────────────────────────────────────────
    @async_retry(max_attempts=3, delay=0.5)
    async def get_order(self, order_uuid: str) -> Optional[Dict]:
        """주문 상태 조회"""
        await self._rest_limiter.acquire()
        if self.is_paper:
            return next((o for o in self._paper_orders if o["uuid"] == order_uuid), None)
        try:
            return self._upbit.get_order(order_uuid)
        except Exception as e:
            logger.error(f"주문 조회 실패 ({order_uuid}): {e}")
            return None

    @async_retry(max_attempts=3, delay=0.5)
    async def cancel_order(self, order_uuid: str) -> bool:
        """주문 취소"""
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
            logger.error(f"주문 취소 실패 ({order_uuid}): {e}")
            return False

    @async_retry(max_attempts=3, delay=0.5)
    async def get_open_orders(self, market: str = None) -> List[Dict]:
        """미체결 주문 목록"""
        await self._rest_limiter.acquire()
        if self.is_paper:
            orders = [o for o in self._paper_orders if o["state"] == "wait"]
            if market:
                orders = [o for o in orders if o["market"] == market]
            return orders
        try:
            result = self._upbit.get_order(market, state="wait") if market else []
            return result or []
        except Exception as e:
            logger.error(f"미체결 주문 조회 실패: {e}")
            return []

    # ── 시장 정보 ─────────────────────────────────────────────────
    @async_retry(max_attempts=3, delay=1.0)
    async def get_all_krw_markets(self) -> List[str]:
        """KRW 마켓 전체 목록"""
        await self._rest_limiter.acquire()
        if pyupbit:
            try:
                tickers = pyupbit.get_tickers(fiat="KRW")
                return tickers or []
            except Exception as e:
                logger.error(f"마켓 목록 조회 실패: {e}")
        return []

    # ── 페이퍼 트레이딩 내부 로직 ─────────────────────────────────
    async def _paper_buy(self, market: str, price: float, volume: float,
                         order_type: str) -> Dict:
        """페이퍼 매수 시뮬레이션"""
        # price=0이면 현재가 조회
        if price <= 0:
            price = await self.get_current_price(market) or 0
        if price <= 0:
            logger.warning(f"페이퍼 매수 실패: 가격 조회 불가 ({market})")
            return {"error": "no_price"}
        # volume=0이면 amount_krw로 계산
        if volume <= 0:
            krw_temp = self._paper_balance.get("KRW", 0)
            volume = krw_temp / price * 0.1  # 10% 사용
        fee = price * volume * self.settings.trading.fee_rate
        total_cost = price * volume + fee
        krw = self._paper_balance.get("KRW", 0)
        logger.info(f"🔍 [PAPER_BUY] {market} | price={price:,} | vol={volume:.6f} | cost={total_cost:,.0f} | KRW잔고={krw:,.0f}")

        if krw < total_cost:
            logger.warning(f"페이퍼 매수 실패: 잔고 부족 (필요: {total_cost:,.0f}, 보유: {krw:,.0f})")
            return {"error": "insufficient_balance"}

        # 잔고 업데이트
        self._paper_balance["KRW"] = krw - total_cost
        coin = market.split("-")[1]
        self._paper_balance[coin] = self._paper_balance.get(coin, 0) + volume

        self._order_counter += 1
        order = {
            "uuid": f"paper_{self._order_counter}",
            "market": market,
            "side": "bid",
            "ord_type": order_type,
            "price": str(price),
            "volume": str(volume),
            "executed_volume": str(volume),
            "state": "done",
            "created_at": time.time(),
            "fee": fee,
        }
        self._paper_orders.append(order)
        logger.info(f"📝 [PAPER] 매수 체결 | {market} | {price:,} × {volume:.6f} (수수료: {fee:,.0f}KRW)")
        return order

    async def _paper_sell(self, market: str, price: float, volume: float,
                          order_type: str) -> Dict:
        """페이퍼 매도 시뮬레이션"""
        coin = market.split("-")[1]
        held = self._paper_balance.get(coin, 0)

        if held < volume:
            volume = held  # 보유량만큼만 매도
        if volume <= 0:
                return {"error": "no_balance"}

        fee = price * volume * self.settings.trading.fee_rate
        proceeds = price * volume - fee

        self._paper_balance["KRW"] = self._paper_balance.get("KRW", 0) + proceeds
        self._paper_balance[coin] = held - volume

        self._order_counter += 1
        order = {
            "uuid": f"paper_{self._order_counter}",
            "market": market,
            "side": "ask",
            "ord_type": order_type,
            "price": str(price),
            "volume": str(volume),
            "executed_volume": str(volume),
            "state": "done",
            "created_at": time.time(),
            "fee": fee,
        }
        self._paper_orders.append(order)
        logger.info(f"📝 [PAPER] 매도 체결 | {market} | {price:,} × {volume:.6f} (수수료: {fee:,.0f}KRW)")
        return order

    # ── 상태 정보 ─────────────────────────────────────────────────

    async def get_balances(self) -> list[dict]:
        """
        SmartWallet 용 잔고 조회.
        반환 형식: [{"currency": "ETH", "balance": "0.001",
                     "avg_buy_price": "4200000", "current_price": 0}]
        페이퍼 모드: 내부 positions 딕셔너리에서 변환
        실거래 모드: 업비트 API get_balances() 호출
        """
        try:
            if getattr(self, 'mode', 'paper') == 'paper' or not getattr(self, '_upbit', None):
                # 페이퍼 모드: positions 에서 list[dict] 생성
                result = []
                positions = getattr(self, 'positions', {})
                for symbol, pos in positions.items():
                    coin = symbol.replace("KRW-", "")
                    qty  = pos.get("qty", 0) if isinstance(pos, dict) else getattr(pos, "qty", 0)
                    avg  = pos.get("avg_price", 0) if isinstance(pos, dict) else getattr(pos, "avg_price", 0)
                    if qty > 1e-10:
                        result.append({
                            "currency"     : coin,
                            "balance"      : str(qty),
                            "avg_buy_price": str(avg),
                            "current_price": 0,
                        })
                return result
            else:
                # 실거래 모드: pyupbit API
                raw = self._upbit.get_balances()
                if not raw or not isinstance(raw, list):
                    return []
                return [
                    {
                        "currency"     : b.get("currency", ""),
                        "balance"      : str(b.get("balance", 0)),
                        "avg_buy_price": str(b.get("avg_buy_price", 0)),
                        "current_price": 0,
                    }
                    for b in raw
                    if b.get("currency") not in ("KRW", "")
                ]
        except Exception as e:
            from loguru import logger
            logger.warning(f"get_balances 오류: {e}")
            return []


    def sync_paper_balance(self, krw_balance: float, positions: dict):
        """포지션 복원 후 페이퍼 잔고 동기화"""
        if not self.is_paper:
            return
        self._paper_balance["KRW"] = krw_balance
        for market, pos in positions.items():
            coin = market.split("-")[1]
            volume = pos.get("volume", pos.get("qty", 0))
            if volume > 0:
                self._paper_balance[coin] = volume
        logger.info(
            f"🔄 페이퍼 잔고 동기화 완료 | KRW=₩{krw_balance:,.0f} | "
            f"코인={len(positions)}종목"
        )

    def get_paper_portfolio_summary(self) -> Dict:
        """페이퍼 트레이딩 포트폴리오 요약"""
        return {
            "mode": "PAPER",
            "balance": self._paper_balance.copy(),
            "total_orders": len(self._paper_orders),
            "executed_orders": sum(1 for o in self._paper_orders if o["state"] == "done"),
        }
