# execution/order_executor.py
from dataclasses import dataclass, field
from typing import Optional
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ExecutionRequest:
    """docstring"""
    market: str
    side: str                        # "BUY" or "SELL"
    amount_krw: float = 0.0
    volume: float = 0.0
    price: float = 0.0
    strategy: str = ""
    strategy_name: str = ""   # strategy 별칭 (하위 호환)
    reason: str = ""
    memo: str = ''                    # 메모
    take_profit: float = 0.0          # 익절가
    stop_loss: float = 0.0            # 손절가
    limit_price: float = 0.0         # 지정가 (0=시장가)
    order_type: str = "market"
    metadata: dict = field(default_factory=dict)


class OrderExecutor:
    """—"""

    def __init__(self, adapter=None, settings: dict = None):
        self.adapter = adapter
        self.settings = settings or {}

    async def execute(self, request: ExecutionRequest) -> dict:
        logger.info(
            f"[OrderExecutor] {request.side} | {request.market} "
            f"| ₩{request.amount_krw:,.0f} | {request.strategy}"
        )
        return {
            "market": request.market,
            "side": request.side,
            "amount_krw": request.amount_krw,
            "volume": request.volume,
            "price": request.price,
            "strategy": request.strategy,
            "reason": request.reason,
            "status": "paper_filled",
        }

    async def execute_buy(self, market: str, amount_krw: float, price: float,
                          strategy: str = "", reason: str = "") -> dict:
        req = ExecutionRequest(
            market=market, side="BUY", amount_krw=amount_krw,
            price=price, strategy=strategy, reason=reason
        )
        return await self.execute(req)

    async def execute_sell(self, market: str, volume: float, price: float,
                           strategy: str = "", reason: str = "") -> dict:
        req = ExecutionRequest(
            market=market, side="SELL", volume=volume,
            price=price, strategy=strategy, reason=reason
        )
        return await self.execute(req)

    async def cancel_all(self, market: str):
        logger.debug(f"[OrderExecutor] cancel_all: {market}")

    async def get_open_orders(self, market: str) -> list:
        return []


from enum import Enum

class OrderSide(str, Enum):
    """docstring"""
    BUY  = "BUY"
    SELL = "SELL"
