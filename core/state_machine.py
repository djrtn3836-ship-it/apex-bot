"""
APEX BOT - 상태 머신
봇의 생명주기 관리 (IDLE → RUNNING → PAUSED → STOPPED)
"""
from enum import Enum, auto
from datetime import datetime, timedelta
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class BotState(Enum):
    IDLE        = "대기중"
    INITIALIZING = "초기화중"
    RUNNING     = "실행중"
    PAUSED      = "일시정지"
    STOPPED     = "중지됨"
    ERROR       = "오류"
    CIRCUIT_BREAK = "서킷브레이커"


class StateMachine:
    """
    봇 상태 머신
    - 유효한 상태 전환만 허용
    - 서킷브레이커 자동 관리
    - 재시작 타이머 관리
    """

    # 허용된 상태 전환 맵
    VALID_TRANSITIONS = {
        BotState.IDLE:          [BotState.INITIALIZING, BotState.STOPPED],
        BotState.INITIALIZING:  [BotState.RUNNING, BotState.ERROR, BotState.STOPPED],
        BotState.RUNNING:       [BotState.PAUSED, BotState.STOPPED, BotState.ERROR, BotState.CIRCUIT_BREAK],
        BotState.PAUSED:        [BotState.RUNNING, BotState.STOPPED],
        BotState.STOPPED:       [BotState.INITIALIZING],
        BotState.ERROR:         [BotState.INITIALIZING, BotState.STOPPED],
        BotState.CIRCUIT_BREAK: [BotState.RUNNING, BotState.STOPPED],
    }

    def __init__(self):
        self._state: BotState = BotState.IDLE
        self._previous_state: Optional[BotState] = None
        self._state_changed_at: datetime = datetime.now()
        self._circuit_break_until: Optional[datetime] = None
        self._pause_reason: str = ""

    @property
    def state(self) -> BotState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state == BotState.RUNNING

    @property
    def is_paused(self) -> bool:
        return self._state in [BotState.PAUSED, BotState.CIRCUIT_BREAK]

    @property
    def can_trade(self) -> bool:
        """거래 가능 여부"""
        if self._state != BotState.RUNNING:
            return False
        if self._circuit_break_until and datetime.now() < self._circuit_break_until:
            return False
        return True

    def transition(self, new_state: BotState, reason: str = "") -> bool:
        """상태 전환"""
        if new_state not in self.VALID_TRANSITIONS.get(self._state, []):
            logger.warning(
                f"⚠️ 유효하지 않은 상태 전환: {self._state.name} → {new_state.name}"
            )
            return False

        self._previous_state = self._state
        self._state = new_state
        self._state_changed_at = datetime.now()
        self._pause_reason = reason

        emoji_map = {
            BotState.RUNNING: "🟢",
            BotState.PAUSED: "🟡",
            BotState.STOPPED: "🔴",
            BotState.ERROR: "❌",
            BotState.CIRCUIT_BREAK: "🚨",
            BotState.INITIALIZING: "🔄",
            BotState.IDLE: "⚪",
        }
        emoji = emoji_map.get(new_state, "")
        logger.info(f"{emoji} 상태 전환: {self._previous_state.name} → {new_state.name} | {reason}")
        return True

    def activate_circuit_breaker(self, hours: int = 48, reason: str = "드로다운 한도 초과"):
        """서킷브레이커 발동"""
        self._circuit_break_until = datetime.now() + timedelta(hours=hours)
        self.transition(BotState.CIRCUIT_BREAK, reason)
        logger.critical(
            f"🚨 서킷브레이커 발동! {hours}시간 거래 중단\n"
            f"   사유: {reason}\n"
            f"   재시작 예정: {self._circuit_break_until.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def check_circuit_breaker_reset(self) -> bool:
        """서킷브레이커 해제 여부 확인"""
        if (self._state == BotState.CIRCUIT_BREAK and
                self._circuit_break_until and
                datetime.now() >= self._circuit_break_until):
            self.transition(BotState.RUNNING, "서킷브레이커 자동 해제")
            logger.info("✅ 서킷브레이커 해제, 거래 재개")
            return True
        return False

    @property
    def uptime(self) -> timedelta:
        return datetime.now() - self._state_changed_at

    def get_status(self) -> dict:
        remaining = None
        if self._circuit_break_until:
            remaining = max(0, (self._circuit_break_until - datetime.now()).total_seconds())
        return {
            "state": self._state.name,
            "state_label": self._state.value,
            "can_trade": self.can_trade,
            "uptime_seconds": self.uptime.total_seconds(),
            "pause_reason": self._pause_reason,
            "circuit_break_remaining_seconds": remaining,
        }
