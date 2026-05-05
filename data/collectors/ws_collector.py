"""APEX BOT -  WebSocket   
  WebSocket API  
-   /  /  
-   + Heartbeat
- Rate Limit  ( 5 ,  5 )"""
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
    """WebSocket 
    wss://api.upbit.com/websocket/v1"""
    WS_URL = "wss://api.upbit.com/websocket/v1"
    PING_INTERVAL = 30          # 30초마다 PING (업비트 권장)
    RECONNECT_DELAY = 5         # 재연결 대기 시간 (초)
    MAX_RECONNECT_ATTEMPTS = 10 # 최대 재연결 시도 횟수

    def __init__(self, markets: List[str], on_message: Callable, on_error: Optional[Callable] = None):
        """Args:
            markets:    (: ["KRW-BTC", "KRW-ETH"])
            on_message:    (async)
            on_error:   (async)"""
        self.markets = markets
        self.on_message = on_message
        self.on_error = on_error
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._reconnect_count = 0
        self._last_message_time = time.time()
        self._message_count = 0
        self._subscription_types: List[str] = []
        self._need_resubscribe: bool = False

    def subscribe_ticker(self) -> "UpbitWebSocketCollector":
        """(ticker)"""
        self._subscription_types.append("ticker")
        return self

    def subscribe_trade(self) -> "UpbitWebSocketCollector":
        """(trade)"""
        self._subscription_types.append("trade")
        return self

    def subscribe_orderbook(self) -> "UpbitWebSocketCollector":
        """(orderbook)"""
        self._subscription_types.append("orderbook")
        return self

    def add_markets(self, new_markets: list) -> bool:
        """—"""
        added = []
        for m in new_markets:
            if m not in self.markets:
                self.markets.append(m)
                added.append(m)
        if added:
            self._need_resubscribe = True
            logger.info(f" WebSocket   : {added} ( )")
            return True
        return False

    async def resubscribe(self):
        """(     )"""
        if self._ws and not self._ws.closed:
            try:
                subscribe_msg = self._build_subscribe_message()
                await self._ws.send(subscribe_msg)
                self._need_resubscribe = False
                logger.info(f" WebSocket   |  {len(self.markets)}개 코인")
            except Exception as e:
                logger.warning(f" WebSocket  : {e}")

    def _build_subscribe_message(self) -> str:
        """WebSocket"""
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
        """WebSocket"""
        subscribe_msg = self._build_subscribe_message()

        async with websockets.connect(
            self.WS_URL,
            ping_interval=self.PING_INTERVAL,
            ping_timeout=10,
            close_timeout=10,
            max_size=2**20,          # 1MB 메시지 허용
        ) as ws:
            self._ws = ws
            self._reconnect_count = 0
            logger.info(f" WebSocket   | : {len(self.markets)}개")

            # 구독 메시지 전송
            await ws.send(subscribe_msg)
            logger.info(f"  : {self._subscription_types}")

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
                    logger.warning(f" JSON  : {e}")
                except Exception as e:
                    logger.error(f"   : {e}")
                    if self.on_error:
                        await self.on_error(e)

    async def run(self):
        """run 실행"""
        self._running = True
        logger.info(" WebSocket  ")

        while self._running:
            try:
                await self.connect()
            except ConnectionClosed as e:
                if not self._running:
                    break
                logger.warning(f" WebSocket  : {e} |  ...")
            except WebSocketException as e:
                logger.error(f" WebSocket : {e}")
            except Exception as e:
                logger.error(f"   : {e}")

            if not self._running:
                break

            self._reconnect_count += 1
            if self._reconnect_count > self.MAX_RECONNECT_ATTEMPTS:
                logger.critical("    !")
                if self.on_error:
                    await self.on_error(Exception("Max reconnect attempts exceeded"))
                break

            delay = min(self.RECONNECT_DELAY * (2 ** min(self._reconnect_count - 1, 5)), 60)
            logger.info(f" {delay}    ({self._reconnect_count}/{self.MAX_RECONNECT_ATTEMPTS})")
            await asyncio.sleep(delay)

    async def stop(self):
        """stop 실행"""
        self._running = False
        if self._ws:
            await self._ws.close()
        logger.info(f" WebSocket   ( : {self._message_count})")

    @property
    def is_healthy(self) -> bool:
        """(60    )"""
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
    """( 5  ) 
       WebSocket"""
    MAX_MARKETS_PER_STREAM = 20  # 스트림당 최대 20개 코인

    # 업비트 WebSocket 연결 제한: 초당 5회 → 연결 간격 최소 0.25초
    # 429 방지를 위해 실제 연결 완료 후 대기 (create_task 후 sleep)
    CONNECT_INTERVAL = 0.5   # 스트림 간 연결 간격 (초) — 429 방지
    CONNECT_WAIT     = 1.0   # 연결 후 안정화 대기 (초)

    def __init__(self, all_markets: List[str], on_candle: Callable,
                 on_trade: Callable, on_orderbook: Callable,
                 target_markets: Optional[List[str]] = None):
        self.all_markets    = all_markets
        # orderbook 구독 대상: target_markets(10개)만, 없으면 all_markets
        self.target_markets = target_markets or all_markets
        self.on_candle      = on_candle
        self.on_trade       = on_trade
        self.on_orderbook   = on_orderbook
        self._collectors: List[UpbitWebSocketCollector] = []
        self._tasks: List[asyncio.Task] = []
        self._started: bool = False  # start() 호출 여부 플래그

    def _chunk_markets(self) -> List[List[str]]:
        """_chunk_markets 실행"""
        chunks = []
        for i in range(0, len(self.all_markets), self.MAX_MARKETS_PER_STREAM):
            chunks.append(self.all_markets[i:i + self.MAX_MARKETS_PER_STREAM])
        return chunks

    async def _message_router(self, data: dict):
        """_message_router 실행"""
        msg_type = data.get("ty", data.get("type", ""))

        if msg_type == "ticker":
            await self.on_candle(data)
        elif msg_type == "trade":
            await self.on_trade(data)
        elif msg_type == "orderbook":
            await self.on_orderbook(data)

    async def start(self):
        """start 실행
        [WS-2] 변경사항:
          - subscribe_trade() 제거: _on_ws_trade=pass로 미사용, 불필요한 데이터
          - orderbook 구독: target_markets(10개)만, 전체 254개 → 불필요
          - CONNECT_INTERVAL(0.5s): create_task 후 실제 연결 간격 보장
          - CONNECT_WAIT(1.0s): 첫 3개 스트림 이후 안정화 대기
        """
        self._started = True  # 즉시 플래그 (WS-WATCH 재진입 방지)
        chunks       = self._chunk_markets()
        # orderbook 전용 스트림: target_markets만 (10개)
        ob_collector = (
            UpbitWebSocketCollector(list(self.target_markets), self._message_router)
            .subscribe_orderbook()
        )
        self._collectors.append(ob_collector)
        ob_task = asyncio.create_task(ob_collector.run(), name="stream_orderbook")
        self._tasks.append(ob_task)
        await asyncio.sleep(self.CONNECT_INTERVAL)

        logger.info(
            f"[WS-START] {len(chunks)}개 ticker 스트림 + 1개 orderbook 스트림 "
            f"| 총 {len(self.all_markets)}개 마켓"
        )

        for i, chunk in enumerate(chunks):
            collector = (
                UpbitWebSocketCollector(chunk, self._message_router)
                .subscribe_ticker()
                # [WS-2] trade 제거: engine.py _on_ws_trade = pass (미사용)
            )
            self._collectors.append(collector)
            task = asyncio.create_task(collector.run(), name=f"stream_ticker_{i}")
            self._tasks.append(task)
            # [WS-2] 실제 연결 간격 보장: 업비트 초당 5회 제한 → 0.5s 간격
            await asyncio.sleep(self.CONNECT_INTERVAL)
            # 처음 3개 스트림 이후 추가 안정화 대기
            if i == 2:
                logger.info("[WS-START] 초기 3개 스트림 연결 완료 → 1초 안정화 대기")
                await asyncio.sleep(self.CONNECT_WAIT)

    async def stop(self):
        """stop 실행"""
        self._started = False
        for collector in self._collectors:
            await collector.stop()
        for task in self._tasks:
            task.cancel()

    # ── 호환 인터페이스 (UpbitWebSocketCollector와 동일한 API) ──────────
    @property
    def _running(self) -> bool:
        """하위 스트림 중 하나라도 실행 중이면 True
        [WS-3] _started 단독 의존 제거:
          _started=True이지만 모든 collector 종료 → False 반환 (재시작 감지 가능)
        """
        if not self._collectors:
            return self._started  # 아직 start() 전: _started 기준
        return any(c._running for c in self._collectors)

    async def run(self):
        """engine_schedule.py 호환용 run() → start() 래퍼
        [WS-4] 재시작 시 _collectors/_tasks 초기화 후 start() 재호출
        """
        if not self._collectors:
            await self.start()
        else:
            # 재시작 감지: 모든 collector 종료 → 초기화 후 재시작
            if not any(c._running for c in self._collectors):
                logger.info("[WS-RUN] 모든 스트림 종료 감지 → collectors 초기화 후 재시작")
                self._collectors.clear()
                self._tasks.clear()
                self._started = False
                await self.start()
            elif self._tasks:
                # 실행 중: 모든 스트림 태스크가 끝날 때까지 대기
                await asyncio.gather(*self._tasks, return_exceptions=True)


    def get_health_status(self) -> List[dict]:
        return [c.stats for c in self._collectors]

# alias
WebSocketCollector = UpbitWebSocketCollector