"""APEX BOT -   
 +   +"""
import time
import asyncio
from typing import Dict, Tuple
from loguru import logger

from config.settings import get_settings
from utils.logger import log_risk


class RiskManager:
    """:
    L1:   5% →    
    L2:  10% → 48  
    L3:   15% →   
    L4:   5 → 24"""

    def __init__(self):
        self.settings = get_settings()
        self.risk_cfg = self.settings.risk

        # 상태 변수
        self._consecutive_losses = 0
        self._daily_trades = 0
        self._daily_wins = 0
        self._paused_until: float = 0.0
        self._pause_reason: str = ""

        # 서킷브레이커 상태
        self._cb_level = 0  # 0=정상, 1~4=레벨
        self._trade_results: list = []  # 최근 거래 결과 (True/False)

        logger.info("   ")

    async def can_open_position(
        self,
        market: str,
        available_capital: float,
        current_positions: int,
    ) -> Tuple[bool, str]:
        """Returns:
            ( , )"""
        # 일시정지 상태
        if self._is_paused():
            remaining = int(self._paused_until - time.time())
            return False, f"서킷브레이커 활성 ({remaining}초 남음): {self._pause_reason}"

        # 최대 포지션 수 확인
        max_pos = self.settings.trading.max_positions
        if current_positions >= max_pos:
            return False, f"최대 포지션 도달 ({current_positions}/{max_pos})"

        # 최소 자본 확인
        if available_capital < self.risk_cfg.min_position_size:
            return False, f"가용 자본 부족 (₩{available_capital:,.0f})"

        # 전체 노출 한도 (자본의 80%)
        # (실제로는 포트폴리오 매니저에서 계산)

        return True, "OK"

    async def check_circuit_breaker(
        self,
        current_drawdown: float,
        total_value: float,
    ) -> bool:
        """Returns:
            True →   ( )"""
        # L2: 드로다운 10% → 48시간 중단
        if current_drawdown >= self.risk_cfg.total_drawdown_limit * 100:
            await self._trigger_circuit_breaker(
                level=2,
                duration=172800,  # 48시간
                reason=f"드로다운 {current_drawdown:.1f}% 초과"
            )
            return True

        # L4: 연속 손실 5회
        if self._consecutive_losses >= self.risk_cfg.consecutive_loss_limit:
            await self._trigger_circuit_breaker(
                level=4,
                duration=86400,  # 24시간
                reason=f"연속 손실 {self._consecutive_losses}회"
            )
            return True

        return False

    async def check_daily_loss_limit(self, daily_pnl: float) -> bool:
        """docstring"""
        if daily_pnl <= -(self.risk_cfg.daily_loss_limit * 100):
            await self._trigger_circuit_breaker(
                level=1,
                duration=28800,  # 8시간 (당일 장 마감까지)
                reason=f"일일 손실 한도 초과 ({daily_pnl:.2f}%)"
            )
            return True
        return False

    def record_trade_result(self, is_win: bool):
        """docstring"""
        self._trade_results.append(is_win)
        if len(self._trade_results) > 20:
            self._trade_results = self._trade_results[-20:]

        if is_win:
            self._consecutive_losses = 0
            self._daily_wins += 1
        else:
            self._consecutive_losses += 1

        self._daily_trades += 1

        logger.debug(
            f"   | {' ' if is_win else ' '} | "
            f"={self._consecutive_losses} | "
            f"={self._calc_recent_win_rate():.1f}%"
        )

    def get_kelly_params(self) -> Dict:
        """Kelly Criterion"""
        if len(self._trade_results) < 10:
            return {"win_rate": 0.5, "avg_win": 0.02, "avg_loss": 0.01}

        wins = sum(self._trade_results)
        total = len(self._trade_results)
        return {
            "win_rate": wins / total,
            "recent_consecutive_losses": self._consecutive_losses,
            "recent_win_rate": self._calc_recent_win_rate(),
        }

    def _calc_recent_win_rate(self) -> float:
        if not self._trade_results:
            return 0.0
        recent = self._trade_results[-10:]
        return sum(recent) / len(recent) * 100

    async def _trigger_circuit_breaker(self, level: int, duration: float, reason: str):
        """docstring"""
        if self._cb_level >= level:
            return  # 이미 더 높은 레벨 활성화

        self._cb_level = level
        self._paused_until = time.time() + duration
        self._pause_reason = reason

        log_risk(
            f"서킷브레이커 L{level}",
            f"{reason} | {int(duration/3600)}시간 중단"
        )
        logger.critical(
            f"  L{level}  | {reason} | "
            f"{duration/3600:.0f}  "
        )

    def _is_paused(self) -> bool:
        """docstring"""
        if self._paused_until <= 0:
            return False
        if time.time() >= self._paused_until:
            # 자동 해제
            self._paused_until = 0
            self._cb_level = 0
            self._pause_reason = ""
            self._consecutive_losses = 0
            logger.info("    -  ")
            return False
        return True

    def force_resume(self):
        """( )"""
        self._paused_until = 0
        self._cb_level = 0
        self._pause_reason = ""
        logger.warning("   ")

    def reset_daily(self):
        """()"""
        self._daily_trades = 0
        self._daily_wins = 0
        if self._cb_level == 1:  # L1만 일일 초기화
            self._paused_until = 0
            self._cb_level = 0

    def get_status(self) -> Dict:
        """docstring"""
        return {
            "circuit_breaker_level": self._cb_level,
            "is_paused": self._is_paused(),
            "paused_reason": self._pause_reason,
            "consecutive_losses": self._consecutive_losses,
            "daily_trades": self._daily_trades,
            "daily_wins": self._daily_wins,
            "recent_win_rate": self._calc_recent_win_rate(),
        }
