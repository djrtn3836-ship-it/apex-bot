"""
APEX BOT - 이벤트 버스 (Event-Driven Architecture 핵심)
asyncio 기반 pub/sub 패턴으로 모듈 간 완전한 디커플링 구현
"""
import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Callable, Coroutine, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class EventType(Enum):
    """이벤트 타입 정의"""
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
    """이벤트 데이터 구조"""
    type: EventType
    data: Any
    source: str = "unknown"
    timestamp: datetime = field(default_factory=datetime.now)
    priority: int = 5              # 1(최고) ~ 10(최저)

    def __lt__(self, other):
        return self.priority < other.priority


class EventBus:
    """
    비동기 이벤트 버스
    - asyncio.Queue 기반 우선순위 처리
    - 다중 구독자 지원
    - 에러 격리 (한 구독자 실패가 다른 구독자에 영향 없음)
    """

    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable]] = defaultdict(list)
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._running: bool = False
        self._processed_count: int = 0
        self._error_count: int = 0

    def subscribe(self, event_type: EventType, handler: Callable[..., Coroutine]):
        """이벤트 구독 등록"""
        self._subscribers[event_type].append(handler)
        logger.debug(f"📌 구독 등록: {event_type.name} → {handler.__qualname__}")

    def unsubscribe(self, event_type: EventType, handler: Callable):
        """이벤트 구독 해제"""
        if handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)

    async def publish(self, event: Event):
        """이벤트 발행 (비동기 큐에 추가)"""
        await self._queue.put((event.priority, event))

    async def publish_sync(self, event: Event):
        """이벤트 즉시 처리 (동기식, 고우선순위용)"""
        await self._dispatch(event)

    async def _dispatch(self, event: Event):
        """구독자들에게 이벤트 전달"""
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
        """안전한 핸들러 호출 (에러 격리)"""
        try:
            await handler(event)
        except Exception as e:
            self._error_count += 1
            logger.error(f"❌ 이벤트 핸들러 오류 [{handler.__qualname__}]: {e}")
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
        """이벤트 처리 루프 시작"""
        self._running = True
        logger.info("🚀 이벤트 버스 시작")

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
                logger.error(f"❌ 이벤트 버스 오류: {e}")

        logger.info(f"🛑 이벤트 버스 종료 (처리: {self._processed_count}, 에러: {self._error_count})")

    async def stop(self):
        """이벤트 버스 중지"""
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
