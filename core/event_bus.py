"""APEX BOT -   (Event-Driven Architecture )
asyncio  pub/sub"""
import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Callable, Coroutine, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class EventType(Enum):
    """docstring"""
    # 데이터 이벤트
    CANDLE_UPDATED    = auto()   # 새 캔들 데이터
    TICK_RECEIVED     = auto()   # 체결 틱
    ORDERBOOK_UPDATED = auto()   # 호가창 업데이트
    
    # 신호 이벤트
    SIGNAL_GENERATED  = auto()   # 전략 신호 생성
    SIGNAL_CONFIRMED  = auto()   # 앙상블 신호 확정
    
    # 주문 이벤트
    ORDER_REQUESTED   = auto()   # 주문 요청
    ORDER_PLACED      = auto()   # 주문 접수
    ORDER_FILLED      = auto()   # 주문 체결
    ORDER_CANCELLED   = auto()   # 주문 취소
    ORDER_FAILED      = auto()   # 주문 실패
    
    # 포지션 이벤트
    POSITION_OPENED   = auto()   # 포지션 진입
    POSITION_CLOSED   = auto()   # 포지션 청산
    POSITION_UPDATED  = auto()   # 포지션 업데이트
    
    # 리스크 이벤트
    STOP_LOSS_HIT     = auto()   # 손절 발동
    TAKE_PROFIT_HIT   = auto()   # 익절 발동
    TRAILING_STOP_HIT = auto()   # 트레일링 스탑 발동
    DRAWDOWN_ALERT    = auto()   # 드로다운 경고
    CIRCUIT_BREAKER   = auto()   # 서킷브레이커 발동
    
    # 시스템 이벤트
    BOT_STARTED       = auto()
    BOT_STOPPED       = auto()
    BOT_PAUSED        = auto()
    ERROR_OCCURRED    = auto()
    HEARTBEAT         = auto()


@dataclass
class Event:
    """docstring"""
    type: EventType
    data: Any
    source: str = "unknown"
    timestamp: datetime = field(default_factory=datetime.now)
    priority: int = 5              # 1(최고) ~ 10(최저)

    def __lt__(self, other):
        return self.priority < other.priority


class EventBus:
    """- asyncio.Queue   
    -   
    -   (      )"""

    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable]] = defaultdict(list)
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._running: bool = False
        self._processed_count: int = 0
        self._error_count: int = 0

    def subscribe(self, event_type: EventType, handler: Callable[..., Coroutine]):
        """docstring"""
        self._subscribers[event_type].append(handler)
        logger.debug(f"  : {event_type.name} → {handler.__qualname__}")

    def unsubscribe(self, event_type: EventType, handler: Callable):
        """docstring"""
        if handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)

    async def publish(self, event: Event):
        """(  )"""
        await self._queue.put((event.priority, event))

    async def publish_sync(self, event: Event):
        """(, )"""
        await self._dispatch(event)

    async def _dispatch(self, event: Event):
        """docstring"""
        handlers = self._subscribers.get(event.type, [])
        if not handlers:
            return

        tasks = []
        for handler in handlers:
            task = asyncio.create_task(
                self._safe_call(handler, event),
                name=f"{event.type.name}_{handler.__name__}"
            )
            tasks.append(task)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_call(self, handler: Callable, event: Event):
        """( )"""
        try:
            await handler(event)
        except Exception as e:
            self._error_count += 1
            logger.error(f"    [{handler.__qualname__}]: {e}")
            # 에러 이벤트 발행 (재귀 방지)
            if event.type != EventType.ERROR_OCCURRED:
                error_event = Event(
                    type=EventType.ERROR_OCCURRED,
                    data={"handler": handler.__qualname__, "error": str(e), "original_event": event.type.name},
                    source="event_bus",
                    priority=1
                )
                await self._queue.put((1, error_event))

    async def run(self):
        """docstring"""
        self._running = True
        logger.info("   ")

        while self._running:
            try:
                priority, event = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
                await self._dispatch(event)
                self._processed_count += 1
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"   : {e}")

        logger.info(f"    (: {self._processed_count}, : {self._error_count})")

    async def stop(self):
        """docstring"""
        self._running = False

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def stats(self) -> dict:
        return {
            "processed": self._processed_count,
            "errors": self._error_count,
            "queue_size": self._queue.qsize(),
            "subscribers": {k.name: len(v) for k, v in self._subscribers.items()}
        }
