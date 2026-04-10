"""APEX BOT -   
 +   +"""
import asyncio
import time
from typing import Dict, List, Optional, Callable
from enum import Enum
from dataclasses import dataclass, field
from loguru import logger


class AlertLevel(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    EMERGENCY = "EMERGENCY"


@dataclass
class Alert:
    """docstring"""
    level: AlertLevel
    category: str       # TRADE / RISK / SYSTEM / PERFORMANCE
    title: str
    message: str
    market: str = ""
    timestamp: float = field(default_factory=time.time)
    sent: bool = False

    def to_dict(self) -> dict:
        return {
            "level": self.level.value,
            "category": self.category,
            "title": self.title,
            "message": self.message,
            "market": self.market,
            "timestamp": self.timestamp,
        }


class AlertManager:
    """-    ()
    -   
    -   
    -   ( )"""

    # 카테고리별 쿨다운 (초)
    COOLDOWN = {
        "TRADE": 10,
        "RISK": 60,
        "SYSTEM": 300,
        "PERFORMANCE": 3600,
    }

    def __init__(self, telegram_notifier=None):
        self._telegram = telegram_notifier
        self._alert_history: List[Alert] = []
        self._last_sent: Dict[str, float] = {}  # {key: timestamp}
        self._handlers: List[Callable] = []
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._running = False

    def add_handler(self, handler: Callable):
        """docstring"""
        self._handlers.append(handler)

    async def start(self):
        """docstring"""
        self._running = True
        asyncio.create_task(self._process_loop())

    async def stop(self):
        self._running = False

    # ── 알림 생성 메서드 ──────────────────────────────────────────
    async def trade(self, action: str, market: str, price: float,
                    amount: float, profit_rate: float = None, strategy: str = ""):
        """docstring"""
        sign = "+" if (profit_rate or 0) >= 0 else ""
        profit_str = f" | 수익률={sign}{profit_rate:.2f}%" if profit_rate is not None else ""
        alert = Alert(
            level=AlertLevel.INFO,
            category="TRADE",
            title=f"{'매수' if action == 'BUY' else '매도'} 체결",
            message=f"{market} | {price:,.0f} | ₩{amount:,.0f}{profit_str} | {strategy}",
            market=market,
        )
        await self._enqueue(alert)

    async def risk_warning(self, event: str, detail: str, market: str = ""):
        """docstring"""
        alert = Alert(
            level=AlertLevel.WARNING,
            category="RISK",
            title=f"리스크 경고: {event}",
            message=detail,
            market=market,
        )
        await self._enqueue(alert)

    async def circuit_breaker(self, level: int, reason: str, duration_h: float):
        """docstring"""
        alert = Alert(
            level=AlertLevel.CRITICAL,
            category="RISK",
            title=f"🚨 서킷브레이커 L{level} 발동",
            message=f"{reason} | {duration_h:.0f}시간 거래 중단",
        )
        await self._enqueue(alert)

    async def system_error(self, error: str, context: str = ""):
        """docstring"""
        alert = Alert(
            level=AlertLevel.CRITICAL,
            category="SYSTEM",
            title="시스템 오류",
            message=f"{error}\n컨텍스트: {context}",
        )
        await self._enqueue(alert)

    async def performance_update(self, stats: Dict):
        """docstring"""
        pnl = stats.get("daily_pnl", 0)
        alert = Alert(
            level=AlertLevel.INFO,
            category="PERFORMANCE",
            title="일일 성과",
            message=(
                f"수익률={pnl:+.2f}% | "
                f"승률={stats.get('win_rate', 0):.1f}% | "
                f"거래={stats.get('total_trades', 0)}회"
            ),
        )
        await self._enqueue(alert)

    # ── 내부 처리 ─────────────────────────────────────────────────
    async def _enqueue(self, alert: Alert):
        """docstring"""
        try:
            await self._queue.put_nowait(alert)
        except asyncio.QueueFull:
            logger.warning("    -  ")

    async def _process_loop(self):
        """docstring"""
        while self._running:
            try:
                alert = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._dispatch(alert)
                self._alert_history.append(alert)
                if len(self._alert_history) > 500:
                    self._alert_history = self._alert_history[-500:]
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"  : {e}")

    async def _dispatch(self, alert: Alert):
        """(  +  )"""
        # 쿨다운 체크
        cooldown_key = f"{alert.category}_{alert.title}"
        cooldown = self.COOLDOWN.get(alert.category, 60)
        last = self._last_sent.get(cooldown_key, 0)

        if alert.level not in (AlertLevel.CRITICAL, AlertLevel.EMERGENCY):
            if time.time() - last < cooldown:
                return  # 쿨다운 중

        self._last_sent[cooldown_key] = time.time()
        alert.sent = True

        # 로그
        log_fn = {
            AlertLevel.INFO: logger.info,
            AlertLevel.WARNING: logger.warning,
            AlertLevel.CRITICAL: logger.error,
            AlertLevel.EMERGENCY: logger.critical,
        }.get(alert.level, logger.info)
        log_fn(f"[{alert.category}] {alert.title}: {alert.message}")

        # 텔레그램 발송
        if self._telegram:
            try:
                if alert.level == AlertLevel.EMERGENCY:
                    await self._telegram.send_emergency_alert(
                        f"{alert.title}\n{alert.message}"
                    )
                elif alert.category == "RISK":
                    await self._telegram.notify_risk(alert.title, alert.message)
                elif alert.category == "SYSTEM":
                    await self._telegram.notify_error(alert.message)
                else:
                    await self._telegram.send_message(
                        f"*{alert.title}*\n{alert.message}"
                    )
            except Exception as e:
                logger.error(f"  : {e}")

        # 커스텀 핸들러
        for handler in self._handlers:
            try:
                await handler(alert)
            except Exception as e:
                logger.error(f"  : {e}")

    def get_recent_alerts(self, n: int = 20,
                           category: str = None) -> List[Dict]:
        """docstring"""
        alerts = self._alert_history
        if category:
            alerts = [a for a in alerts if a.category == category]
        return [a.to_dict() for a in alerts[-n:]]

    def get_alert_stats(self) -> Dict:
        """docstring"""
        by_level = {}
        by_category = {}
        for a in self._alert_history:
            by_level[a.level.value] = by_level.get(a.level.value, 0) + 1
            by_category[a.category] = by_category.get(a.category, 0) + 1
        return {"by_level": by_level, "by_category": by_category}
