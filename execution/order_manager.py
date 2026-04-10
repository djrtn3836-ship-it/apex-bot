"""APEX BOT -  
 // +"""
import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from enum import Enum
import logging

from data.collectors.rest_collector import UpbitRestCollector
from core.event_bus import EventBus, Event, EventType

logger = logging.getLogger(__name__)


class OrderStatus(Enum):
    PENDING   = "pending"
    PLACED    = "placed"
    PARTIAL   = "partial"
    FILLED    = "filled"
    CANCELLED = "cancelled"
    FAILED    = "failed"
    EXPIRED   = "expired"


class OrderSide(Enum):
    BUY  = "bid"
    SELL = "ask"


@dataclass
class Order:
    """docstring"""
    order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    market: str = ""
    side: OrderSide = OrderSide.BUY
    order_type: str = "limit"          # limit | market
    price: Optional[float] = None
    volume: Optional[float] = None
    amount: Optional[float] = None     # KRW 금액 (시장가 매수시)
    strategy: str = ""
    signal_score: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    upbit_uuid: Optional[str] = None   # 업비트 주문 UUID
    filled_price: Optional[float] = None
    filled_volume: Optional[float] = None
    fee: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    filled_at: Optional[datetime] = None
    error_message: str = ""
    retry_count: int = 0
    max_retries: int = 3
    timeout_seconds: int = 30          # 미체결 취소 타임아웃

    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.FILLED

    @property
    def is_active(self) -> bool:
        return self.status in [OrderStatus.PENDING, OrderStatus.PLACED, OrderStatus.PARTIAL]

    @property
    def total_value(self) -> float:
        if self.filled_price and self.filled_volume:
            return self.filled_price * self.filled_volume
        if self.price and self.volume:
            return self.price * self.volume
        return self.amount or 0.0


class Position:
    """docstring"""

    def __init__(
        self,
        market: str,
        entry_price: float,
        volume: float,
        stop_loss: float,
        take_profit: float,
        strategy: str,
        trailing_activation: float = 0.03,
        trailing_distance: float = 0.015,
    ):
        self.position_id = str(uuid.uuid4())
        self.market = market
        self.entry_price = entry_price
        self.volume = volume
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.strategy = strategy
        self.trailing_activation = trailing_activation
        self.trailing_distance = trailing_distance

        self.current_price = entry_price
        self.highest_price = entry_price    # 트레일링 스탑 기준
        self.trailing_stop_price: Optional[float] = None
        self.trailing_active = False
        self.opened_at = datetime.now()
        self.is_open = True

    @property
    def pnl(self) -> float:
        return (self.current_price - self.entry_price) * self.volume

    @property
    def pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        return (self.current_price - self.entry_price) / self.entry_price

    @property
    def invested_amount(self) -> float:
        return self.entry_price * self.volume

    def update_price(self, current_price: float) -> Optional[str]:
        """+   
        Returns:   (None  )"""
        self.current_price = current_price

        # 고점 업데이트
        if current_price > self.highest_price:
            self.highest_price = current_price

        # 트레일링 스탑 활성화 체크
        if not self.trailing_active and self.pnl_pct >= self.trailing_activation:
            self.trailing_active = True
            self.trailing_stop_price = current_price * (1 - self.trailing_distance)
            logger.info(
                f"    | {self.market} | "
                f": {self.pnl_pct:.2%} | : {self.trailing_stop_price:,.0f}"
            )

        # 트레일링 스탑 업데이트
        if self.trailing_active:
            new_trail = self.highest_price * (1 - self.trailing_distance)
            if new_trail > (self.trailing_stop_price or 0):
                self.trailing_stop_price = new_trail

        # ─ 청산 조건 체크 ─
        # 1. 손절
        if current_price <= self.stop_loss:
            return "stop_loss"

        # 2. 트레일링 스탑
        if self.trailing_active and self.trailing_stop_price and current_price <= self.trailing_stop_price:
            return "trailing_stop"

        # 3. 목표가 익절
        if current_price >= self.take_profit:
            return "take_profit"

        return None

    def to_dict(self) -> dict:
        return {
            "position_id": self.position_id,
            "market": self.market,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "volume": self.volume,
            "invested_amount": self.invested_amount,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "trailing_stop": self.trailing_stop_price,
            "trailing_active": self.trailing_active,
            "pnl": round(self.pnl, 0),
            "pnl_pct": round(self.pnl_pct, 4),
            "strategy": self.strategy,
            "opened_at": str(self.opened_at),
        }


class OrderManager:
    """-  //
    -   
    -"""

    def __init__(self, rest_client: UpbitRestCollector, event_bus: EventBus,
                 fee_rate: float = 0.0005, is_paper: bool = False):
        self.rest_client = rest_client
        self.event_bus = event_bus
        self.fee_rate = fee_rate
        self.is_paper = is_paper         # 페이퍼 트레이딩 모드

        self._orders: Dict[str, Order] = {}          # order_id → Order
        self._positions: Dict[str, Position] = {}    # market → Position
        self._order_tracker_task: Optional[asyncio.Task] = None

    # ─── 주문 실행 ─────────────────────────────────────────────────

    async def buy(
        self,
        market: str,
        price: float,
        amount_krw: float,
        stop_loss: float,
        take_profit: float,
        strategy: str = "",
        signal_score: float = 0.0,
    ) -> Optional[Order]:
        """docstring"""
        volume = amount_krw / price
        order = Order(
            market=market,
            side=OrderSide.BUY,
            order_type="limit",
            price=price,
            volume=volume,
            strategy=strategy,
            signal_score=signal_score,
        )

        success = await self._execute_order(order)
        if success and order.is_filled:
            # 포지션 생성
            pos = Position(
                market=market,
                entry_price=order.filled_price or price,
                volume=order.filled_volume or volume,
                stop_loss=stop_loss,
                take_profit=take_profit,
                strategy=strategy,
            )
            self._positions[market] = pos
            await self.event_bus.publish(Event(
                type=EventType.POSITION_OPENED,
                data=pos.to_dict(),
                source="order_manager",
                priority=3
            ))
            logger.info(f"   | {market} | {volume:.8f} @ {price:,.0f}")

        return order

    async def sell(
        self,
        market: str,
        price: float,
        volume: float,
        reason: str = "signal",
    ) -> Optional[Order]:
        """docstring"""
        order = Order(
            market=market,
            side=OrderSide.SELL,
            order_type="limit",
            price=price,
            volume=volume,
        )

        success = await self._execute_order(order)
        if success and order.is_filled:
            pos = self._positions.pop(market, None)
            if pos:
                final_pnl = order.total_value - pos.invested_amount - order.fee
                await self.event_bus.publish(Event(
                    type=EventType.POSITION_CLOSED,
                    data={**pos.to_dict(), "close_reason": reason, "final_pnl": final_pnl},
                    source="order_manager",
                    priority=3
                ))
                logger.info(
                    f"{'' if final_pnl > 0 else ''}   | {market} | "
                    f"PnL: {final_pnl:+,.0f} KRW ({pos.pnl_pct:+.2%}) | 사유: {reason}"
                )
        return order

    async def _execute_order(self, order: Order) -> bool:
        """docstring"""
        self._orders[order.order_id] = order

        if self.is_paper:
            # 페이퍼 트레이딩: 즉시 체결 시뮬레이션
            order.status = OrderStatus.FILLED
            order.filled_price = order.price
            order.filled_volume = order.volume
            order.fee = (order.filled_price * order.filled_volume) * self.fee_rate
            order.filled_at = datetime.now()
            order.upbit_uuid = f"paper_{order.order_id}"
            logger.info(f" []   | {order.market} {order.side.name}")
            return True

        try:
            result = await self.rest_client.place_order(
                market=order.market,
                side=order.side.value,
                ord_type=order.order_type,
                price=order.price,
                volume=order.volume,
            )
            order.upbit_uuid = result.get("uuid")
            order.status = OrderStatus.PLACED

            await self.event_bus.publish(Event(
                type=EventType.ORDER_PLACED,
                data={"order_id": order.order_id, "upbit_uuid": order.upbit_uuid},
                source="order_manager",
                priority=2
            ))
            return True

        except Exception as e:
            order.status = OrderStatus.FAILED
            order.error_message = str(e)
            logger.error(f"   | {order.market}: {e}")
            await self.event_bus.publish(Event(
                type=EventType.ORDER_FAILED,
                data={"order_id": order.order_id, "error": str(e)},
                source="order_manager",
                priority=2
            ))
            return False

    async def update_positions(self, prices: Dict[str, float]):
        """+"""
        for market, pos in list(self._positions.items()):
            if not pos.is_open:
                continue

            current_price = prices.get(market)
            if not current_price:
                continue

            close_reason = pos.update_price(current_price)
            if close_reason:
                logger.info(f"    | {market} | : {close_reason}")
                if close_reason == "stop_loss":
                    await self.event_bus.publish(Event(
                        type=EventType.STOP_LOSS_HIT,
                        data={"market": market, "price": current_price, **pos.to_dict()},
                        source="order_manager", priority=1
                    ))
                elif close_reason == "take_profit":
                    await self.event_bus.publish(Event(
                        type=EventType.TAKE_PROFIT_HIT,
                        data={"market": market, "price": current_price, **pos.to_dict()},
                        source="order_manager", priority=2
                    ))
                elif close_reason == "trailing_stop":
                    await self.event_bus.publish(Event(
                        type=EventType.TRAILING_STOP_HIT,
                        data={"market": market, "price": current_price, **pos.to_dict()},
                        source="order_manager", priority=2
                    ))

    def get_positions(self) -> List[dict]:
        return [p.to_dict() for p in self._positions.values() if p.is_open]

    def get_position(self, market: str) -> Optional[Position]:
        return self._positions.get(market)

    def has_position(self, market: str) -> bool:
        return market in self._positions and self._positions[market].is_open

    def get_total_exposure(self) -> float:
        return sum(p.invested_amount for p in self._positions.values() if p.is_open)

    def get_open_count(self) -> int:
        return sum(1 for p in self._positions.values() if p.is_open)
