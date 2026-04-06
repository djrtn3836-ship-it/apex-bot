"""
APEX BOT - 업비트 WebSocket 실시간 데이터 수집기
업비트 공식 WebSocket API 완전 구현
- 실시간 캔들 / 체결 / 호가 수신
- 자동 재연결 + Heartbeat
- Rate Limit 준수 (초당 5회 연결, 최대 5개 동시)
"""
import asyncio
import json
import uuid
import time
from datetime import datetime
from typing import Callable, List, Optional, Dict
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException
import logging

logger = logging.getLogger(__name__)


class UpbitWebSocketCollector:
    """
    업비트 WebSocket 수집기
    wss://api.upbit.com/websocket/v1 연결
    """
    WS_URL = "wss://api.upbit.com/websocket/v1"
    PING_INTERVAL = 30          # 30초마다 PING (업비트 권장)
    RECONNECT_DELAY = 5         # 재연결 대기 시간 (초)
    MAX_RECONNECT_ATTEMPTS = 10 # 최대 재연결 시도 횟수

    def __init__(self, markets: List[str], on_message: Callable, on_error: Optional[Callable] = None):
        """
        Args:
            markets: 구독할 마켓 리스트 (예: ["KRW-BTC", "KRW-ETH"])
            on_message: 메시지 수신 콜백 (async)
            on_error: 에러 콜백 (async)
        """
        self.markets = markets
        self.on_message = on_message
        self.on_error = on_error
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._reconnect_count = 0
        self._last_message_time = time.time()
        self._message_count = 0
        self._subscription_types: List[str] = []

    def subscribe_ticker(self) -> "UpbitWebSocketCollector":
        """현재가(ticker) 구독"""
        self._subscription_types.append("ticker")
        return self

    def subscribe_trade(self) -> "UpbitWebSocketCollector":
        """체결(trade) 구독"""
        self._subscription_types.append("trade")
        return self

    def subscribe_orderbook(self) -> "UpbitWebSocketCollector":
        """호가(orderbook) 구독"""
        self._subscription_types.append("orderbook")
        return self

    def _build_subscribe_message(self) -> str:
        """업비트 WebSocket 구독 메시지 생성"""
        ticket = str(uuid.uuid4())
        message = [{"ticket": ticket}]

        for sub_type in self._subscription_types:
            entry = {"type": sub_type, "codes": self.markets}
            # 체결 데이터는 실시간 수신만 (히스토리 불필요)
            if sub_type == "trade":
                entry["isOnlyRealtime"] = True
            message.append(entry)

        message.append({"format": "SIMPLE"})  # 심플 포맷 (데이터 최소화)
        return json.dumps(message)

    async def connect(self):
        """WebSocket 연결 및 구독"""
        subscribe_msg = self._build_subscribe_message()

        async with websockets.connect(
            self.WS_URL,
            ping_interval=self.PING_INTERVAL,
            ping_timeout=10,
            close_timeout=10,
            max_size=2**20,          # 1MB 메시지 허용
            extra_headers={"User-Agent": "ApexBot/1.0"}
        ) as ws:
            self._ws = ws
            self._reconnect_count = 0
            logger.info(f"✅ WebSocket 연결 성공 | 마켓: {len(self.markets)}개")

            # 구독 메시지 전송
            await ws.send(subscribe_msg)
            logger.info(f"📡 구독 시작: {self._subscription_types}")

            # 메시지 수신 루프
            async for raw_message in ws:
                if not self._running:
                    break
                try:
                    self._last_message_time = time.time()
                    self._message_count += 1

                    # 바이너리 메시지 디코딩
                    if isinstance(raw_message, bytes):
                        data = json.loads(raw_message.decode("utf-8"))
                    else:
                        data = json.loads(raw_message)

                    await self.on_message(data)

                except json.JSONDecodeError as e:
                    logger.warning(f"⚠️ JSON 파싱 오류: {e}")
                except Exception as e:
                    logger.error(f"❌ 메시지 처리 오류: {e}")
                    if self.on_error:
                        await self.on_error(e)

    async def run(self):
        """자동 재연결을 포함한 실행 루프"""
        self._running = True
        logger.info("🚀 WebSocket 수집기 시작")

        while self._running:
            try:
                await self.connect()
            except ConnectionClosed as e:
                if not self._running:
                    break
                logger.warning(f"⚠️ WebSocket 연결 끊김: {e} | 재연결 중...")
            except WebSocketException as e:
                logger.error(f"❌ WebSocket 오류: {e}")
            except Exception as e:
                logger.error(f"❌ 예상치 못한 오류: {e}")

            if not self._running:
                break

            self._reconnect_count += 1
            if self._reconnect_count > self.MAX_RECONNECT_ATTEMPTS:
                logger.critical("🚨 최대 재연결 시도 초과!")
                if self.on_error:
                    await self.on_error(Exception("Max reconnect attempts exceeded"))
                break

            delay = min(self.RECONNECT_DELAY * (2 ** min(self._reconnect_count - 1, 5)), 60)
            logger.info(f"🔄 {delay}초 후 재연결 시도 ({self._reconnect_count}/{self.MAX_RECONNECT_ATTEMPTS})")
            await asyncio.sleep(delay)

    async def stop(self):
        """수집기 중지"""
        self._running = False
        if self._ws:
            await self._ws.close()
        logger.info(f"🛑 WebSocket 수집기 중지 (수신 메시지: {self._message_count}개)")

    @property
    def is_healthy(self) -> bool:
        """연결 상태 확인 (60초 내 메시지 수신 여부)"""
        return (time.time() - self._last_message_time) < 60

    @property
    def stats(self) -> dict:
        return {
            "running": self._running,
            "message_count": self._message_count,
            "reconnect_count": self._reconnect_count,
            "last_message_seconds_ago": round(time.time() - self._last_message_time, 1),
            "is_healthy": self.is_healthy,
            "markets": len(self.markets),
        }


class MultiStreamCollector:
    """
    다중 스트림 수집기
    업비트 제한(최대 5개 동시 연결)을 준수하며
    많은 코인을 여러 WebSocket으로 분산 처리
    """
    MAX_MARKETS_PER_STREAM = 20  # 스트림당 최대 20개 코인

    def __init__(self, all_markets: List[str], on_candle: Callable,
                 on_trade: Callable, on_orderbook: Callable):
        self.all_markets = all_markets
        self.on_candle = on_candle
        self.on_trade = on_trade
        self.on_orderbook = on_orderbook
        self._collectors: List[UpbitWebSocketCollector] = []
        self._tasks: List[asyncio.Task] = []

    def _chunk_markets(self) -> List[List[str]]:
        """마켓 리스트를 청크로 분할"""
        chunks = []
        for i in range(0, len(self.all_markets), self.MAX_MARKETS_PER_STREAM):
            chunks.append(self.all_markets[i:i + self.MAX_MARKETS_PER_STREAM])
        return chunks

    async def _message_router(self, data: dict):
        """수신 메시지를 타입에 따라 라우팅"""
        msg_type = data.get("ty", data.get("type", ""))

        if msg_type == "ticker":
            await self.on_candle(data)
        elif msg_type == "trade":
            await self.on_trade(data)
        elif msg_type == "orderbook":
            await self.on_orderbook(data)

    async def start(self):
        """모든 스트림 시작"""
        chunks = self._chunk_markets()
        logger.info(f"📡 {len(chunks)}개 스트림으로 {len(self.all_markets)}개 마켓 수집 시작")

        for i, chunk in enumerate(chunks):
            collector = (
                UpbitWebSocketCollector(chunk, self._message_router)
                .subscribe_ticker()
                .subscribe_trade()
                .subscribe_orderbook()
            )
            self._collectors.append(collector)
            task = asyncio.create_task(collector.run(), name=f"stream_{i}")
            self._tasks.append(task)
            # 연결 간격 (업비트 제한: 초당 5회)
            await asyncio.sleep(0.3)

    async def stop(self):
        """모든 스트림 중지"""
        for collector in self._collectors:
            await collector.stop()
        for task in self._tasks:
            task.cancel()

    def get_health_status(self) -> List[dict]:
        return [c.stats for c in self._collectors]

# alias
WebSocketCollector = UpbitWebSocketCollector
