"""APEX BOT -  
 →   +   +   +"""
import asyncio
import time
from typing import Optional, Dict, List
from enum import Enum
from dataclasses import dataclass, field
from loguru import logger

from config.settings import get_settings
from execution.upbit_adapter import UpbitAdapter
from utils.helpers import calculate_profit_rate

class OrderSide(Enum):
    BUY = "bid"
    SELL = "ask"

class OrderStatus(Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    FAILED = "failed"

@dataclass
class ExecutionRequest:
    """docstring"""
    market: str
    side: OrderSide
    amount_krw: float           # 매수금액 (KRW)
    volume: float = 0.0         # 매도수량 (코인)
    limit_price: Optional[float] = None  # 지정가 (None=시장가)
    reason: str = ""            # 진입/청산 사유
    strategy_name: str = ""
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

@dataclass
class ExecutionResult:
    """docstring"""
    request: ExecutionRequest
    status: OrderStatus
    order_uuid: str = ""
    executed_price: float = 0.0
    executed_volume: float = 0.0
    fee: float = 0.0
    timestamp: float = field(default_factory=time.time)
    error_message: str = ""

    @property
    def executed_amount_krw(self) -> float:
        return self.executed_price * self.executed_volume

class OrderExecutor:
    """-  ,    
    -  3 
    -   ( 60 )
    -"""

    FILL_TIMEOUT = 60       # 체결 대기 최대 60초
    FILL_CHECK_INTERVAL = 2  # 2초마다 체결 확인
    MAX_RETRIES = 3

    def __init__(self, adapter: UpbitAdapter, db_manager=None):
        self.adapter = adapter
        self.db_manager = db_manager  # DB 저장용
        self.settings = get_settings()
        self._active_orders: Dict[str, ExecutionResult] = {}
        self._execution_history: List[ExecutionResult] = []
        self._lock = asyncio.Lock()

    # ── 메인 실행 인터페이스 ──────────────────────────────────────
    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """( )"""
        logger.info(
            f"   | {request.market} | {request.side.name} | "
            f"={request.amount_krw:,.0f}KRW | ={request.reason}"
        )

        result = ExecutionResult(request=request, status=OrderStatus.PENDING)

        for attempt in range(self.MAX_RETRIES):
            try:
                # limit_price 무시 → 항상 시장가 실행
                result = await self._execute_market(request)

                if result.status in (OrderStatus.FILLED, OrderStatus.PARTIAL):
                    break
                elif result.status == OrderStatus.FAILED:
                    if attempt < self.MAX_RETRIES - 1:
                        wait = 2 ** attempt
                        logger.warning(f"  {attempt+1}/{self.MAX_RETRIES} ({wait}s )")
                        await asyncio.sleep(wait)

            except Exception as e:
                logger.error(f"   ( {attempt+1}): {e}")
                result.error_message = str(e)
                if attempt == self.MAX_RETRIES - 1:
                    result.status = OrderStatus.FAILED

        self._execution_history.append(result)
        self._log_execution_result(result)
        return result

    # ── 지정가 주문 ───────────────────────────────────────────────
    async def _execute_limit(self, req: ExecutionRequest) -> ExecutionResult:
        """+"""
        result = ExecutionResult(request=req, status=OrderStatus.SUBMITTED)

        if req.side == OrderSide.BUY:
            order = await self.adapter.buy_limit_order(
                req.market, req.limit_price, req.amount_krw
            )
        else:
            order = await self.adapter.sell_limit_order(
                req.market, req.limit_price, req.volume
            )

        if not order or "error" in order:
            result.status = OrderStatus.FAILED
            result.error_message = str(order)
            return result

        result.order_uuid = order.get("uuid", "")
        async with self._lock:
            self._active_orders[result.order_uuid] = result

        # 체결 대기
        filled = await self._wait_for_fill(result.order_uuid)

        if filled:
            result.status = OrderStatus.FILLED
            result.executed_price = float(filled.get("price", req.limit_price))
            result.executed_volume = float(filled.get("executed_volume", 0))
            result.fee = float(filled.get("fee", filled.get("paid_fee", 0)))
        else:
            # 미체결 → 취소 후 시장가 전환
            logger.warning(f"⏱   →     ")
            await self.adapter.cancel_order(result.order_uuid)
            # 시장가로 재시도
            market_req = ExecutionRequest(
                market=req.market, side=req.side,
                amount_krw=req.amount_krw, volume=req.volume,
                reason=req.reason + " (시장가 전환)"
            )
            return await self._execute_market(market_req)

        async with self._lock:
            self._active_orders.pop(result.order_uuid, None)
        return result

    # ── 시장가 주문 ───────────────────────────────────────────────
    async def _execute_market(self, req: ExecutionRequest) -> ExecutionResult:
        """docstring"""
        result = ExecutionResult(request=req, status=OrderStatus.SUBMITTED)

        if req.side == OrderSide.BUY:
            order = await self.adapter.buy_market_order(req.market, req.amount_krw)
        else:
            order = await self.adapter.sell_market_order(req.market, req.volume)

        if not order or "error" in order:
            result.status = OrderStatus.FAILED
            result.error_message = str(order)
            return result

        result.order_uuid = order.get("uuid", "")
        result.status = OrderStatus.FILLED
        result.executed_volume = float(order.get("executed_volume", 0))
        result.fee = float(order.get("fee", order.get("paid_fee", 0)))
        result.executed_price = float(order.get("price", 0))

        # 시장가 슬리피지 추정
        if result.executed_volume > 0 and result.executed_price == 0:
            # 체결가 재조회
            current_price = await self.adapter.get_current_price(req.market)
            if current_price:
                slippage = self.settings.trading.slippage_rate
                result.executed_price = current_price * (1 + slippage if req.side == OrderSide.BUY else 1 - slippage)

        return result

    # ── 체결 확인 루프 ────────────────────────────────────────────
    async def _wait_for_fill(self, order_uuid: str) -> Optional[Dict]:
        """( FILL_TIMEOUT)"""
        start = time.time()
        while time.time() - start < self.FILL_TIMEOUT:
            await asyncio.sleep(self.FILL_CHECK_INTERVAL)
            order = await self.adapter.get_order(order_uuid)
            if not order:
                continue
            state = order.get("state", "")
            if state == "done":
                return order
            elif state in ("cancel", "cancelled"):
                return None
        return None  # 타임아웃

    # ── 긴급 전량 청산 ────────────────────────────────────────────
    async def emergency_sell_all(self, market: str, reason: str = "긴급 청산") -> ExecutionResult:
        """docstring"""
        coin = market.split("-")[1]
        volume = await self.adapter.get_balance(coin)
        if volume <= 0:
            logger.info(f"   ({market}:  )")
            return ExecutionResult(
                request=ExecutionRequest(market=market, side=OrderSide.SELL,
                                         amount_krw=0, volume=0, reason=reason),
                status=OrderStatus.CANCELLED
            )

        req = ExecutionRequest(
            market=market,
            side=OrderSide.SELL,
            amount_krw=0,
            volume=volume,
            reason=reason,
        )
        logger.warning(f"    | {market} | {volume:.8f} | {reason}")
        return await self._execute_market(req)

    # ── 미체결 주문 전체 취소 ────────────────────────────────────
    async def cancel_all_orders(self, market: str = None) -> int:
        """docstring"""
        orders = await self.adapter.get_open_orders(market)
        cancelled = 0
        for order in orders:
            if await self.adapter.cancel_order(order["uuid"]):
                cancelled += 1
        if cancelled > 0:
            logger.info(f" {cancelled}   ")
        return cancelled

    # ── 통계 ──────────────────────────────────────────────────────
    def get_execution_stats(self) -> Dict:
        """docstring"""
        if not self._execution_history:
            return {}
        filled = [r for r in self._execution_history if r.status == OrderStatus.FILLED]
        failed = [r for r in self._execution_history if r.status == OrderStatus.FAILED]
        return {
            "total_orders": len(self._execution_history),
            "filled": len(filled),
            "failed": len(failed),
            "success_rate": len(filled) / len(self._execution_history) * 100,
            "total_fee": sum(r.fee for r in filled),
        }

    def _log_execution_result(self, result: ExecutionResult):
        """+ DB"""
        if result.status == OrderStatus.FILLED:
            logger.success(
                f"✅ 체결 완료 | {result.request.market} | "
                f"{result.request.side.name} | 체결가={result.executed_price:,.0f} | "
                f"수량={result.executed_volume:.6f} | 수수료={result.fee:,.0f}KRW"
            )
            # ── DB 저장: engine._execute_buy에서 처리 (중복 방지) ──
        elif result.status == OrderStatus.FAILED:
            logger.error(
                f"   | {result.request.market} | {result.error_message}"
            )
