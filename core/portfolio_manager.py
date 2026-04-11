"""APEX BOT -  
   +   +"""
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import numpy as np
from loguru import logger

from config.settings import get_settings
from utils.helpers import calculate_profit_rate, format_percent


@dataclass
class Position:
    """docstring"""
    market: str
    entry_price: float
    volume: float
    amount_krw: float
    entry_time: float = field(default_factory=time.time)
    strategy: str = ""
    stop_loss: float = 0.0
    take_profit: float = 0.0
    peak_price: float = 0.0
    current_price: float = 0.0

    def __post_init__(self):
        self.peak_price = self.entry_price

    @property
    def current_value(self) -> float:
        return self.current_price * self.volume

    @property
    def unrealized_pnl(self) -> float:
        return self.current_value - self.amount_krw

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.amount_krw == 0:
            return 0.0
        return (self.current_value - self.amount_krw) / self.amount_krw * 100

    @property
    def hold_hours(self) -> float:
        return (time.time() - self.entry_time) / 3600


@dataclass
class TradeRecord:
    """docstring"""
    market: str
    side: str
    price: float
    volume: float
    amount_krw: float
    fee: float
    timestamp: float = field(default_factory=time.time)
    strategy: str = ""
    reason: str = ""
    profit_rate: float = 0.0  # DB 저장 단위: % (예: 2.5 = 2.5%)


class PortfolioManager:
    """- profit_rate  : % (close_position *100  )
    - get_statistics()"""

    def __init__(self):
        self.settings = get_settings()
        self._positions: Dict[str, Position] = {}
        self._trade_history: List[TradeRecord] = []
        self._load_trade_history_from_db()
        self._initial_capital: float = 0.0
        self._peak_portfolio_value: float = 0.0
        self._daily_start_value: float = 0.0
        self._daily_start_time: float = time.time()

    def set_initial_capital(self, capital: float):
        self._initial_capital = capital
        self._peak_portfolio_value = capital
        self._daily_start_value = capital

    # ── 포지션 관리 ──────────────────────────────────────────────
    def open_position(
        self,
        market: str,
        entry_price: float,
        volume: float,
        amount_krw: float,
        strategy: str = "",
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
    ):
        position = Position(
            market=market,
            entry_price=entry_price,
            volume=volume,
            amount_krw=amount_krw,
            strategy=strategy,
            stop_loss=stop_loss,
            take_profit=take_profit,
            current_price=entry_price,
        )
        self._positions[market] = position
        logger.info(
            f"   | {market} | {entry_price:,.0f} × {volume:.6f} | "
            f"₩{amount_krw:,.0f} | {strategy}"
        )
        return position

    def close_position(
        self,
        market: str,
        exit_price: float,
        fee: float,
        reason: str = "",
    ) -> Optional[Tuple[float, float]]:
        """→ ( KRW,  %)"""
        position = self._positions.get(market)
        if not position:
            logger.warning(f" : {market}")
            return None

        # profit_rate: % 단위 (예: 2.5 = 2.5%)
        profit_rate = calculate_profit_rate(
            position.entry_price,
            exit_price,
            self.settings.trading.fee_rate,
        )

        proceeds = exit_price * position.volume - fee

        record = TradeRecord(
            market=market,
            side="SELL",
            price=exit_price,
            volume=position.volume,
            amount_krw=position.amount_krw,
            fee=fee,
            strategy=position.strategy,
            reason=reason,
            profit_rate=profit_rate,
        )
        self._trade_history.append(record)
        del self._positions[market]

        logger.info(
            f"   | {market} | ={format_percent(profit_rate)} | "
            f"사유={reason} | 보유={position.hold_hours:.1f}h"
        )
        return proceeds, profit_rate

    def update_prices(self, price_map: Dict[str, float]):
        for market, price in price_map.items():
            if market in self._positions:
                pos = self._positions[market]
                pos.current_price = price
                if price > pos.peak_price:
                    pos.peak_price = price

    # ── 드로다운 / 위험 지표 ─────────────────────────────────────
    def get_total_value(self, krw_balance: float) -> float:
        coin_value = sum(pos.current_value for pos in self._positions.values())
        return krw_balance + coin_value

    def get_current_drawdown(self, total_value: float) -> float:
        if self._peak_portfolio_value <= 0:
            return 0.0
        if total_value > self._peak_portfolio_value:
            self._peak_portfolio_value = total_value
        return (
            (self._peak_portfolio_value - total_value)
            / self._peak_portfolio_value
            * 100
        )

    def get_daily_pnl(self, current_value: float) -> float:
        now = time.time()
        # 자정 기준 일일 초기화
        if now - self._daily_start_time > 86400:
            self._daily_start_value = current_value
            self._daily_start_time = now
        if self._daily_start_value <= 0:
            return 0.0
        return (
            (current_value - self._daily_start_value)
            / self._daily_start_value
            * 100
        )

    # ── 성과 통계 ────────────────────────────────────────────────
    def _load_trade_history_from_db(self):
        """DB"""
        try:
            import sqlite3 as _sq
            conn = _sq.connect("database/apex_bot.db")
            cur = conn.cursor()
            cur.execute(
                """
                SELECT timestamp, market, side, price, volume,
                       amount_krw, fee, profit_rate, strategy, reason
                FROM trade_history
                ORDER BY id ASC
                """
            )
            rows = cur.fetchall()
            conn.close()

            loaded = 0
            for row in rows:
                ts = row[0]
                already = any(
                    getattr(t, "timestamp", None) == ts
                    for t in self._trade_history
                )
                if already:
                    continue
                from types import SimpleNamespace
                t = SimpleNamespace(
                    timestamp=row[0],
                    market=row[1],
                    side=row[2],
                    price=row[3],
                    volume=row[4],
                    amount_krw=row[5],
                    fee=row[6],
                    profit_rate=row[7] or 0.0,  # 이미 % 단위
                    strategy=row[8],
                    reason=row[9],
                )
                self._trade_history.append(t)
                loaded += 1

            if loaded:
                logger.info(f"   DB : {loaded} ")

        except Exception as e:
            logger.warning(f"  DB  : {e}")

    def get_statistics(self) -> Dict:
        """profit_rate : % (DB  ,   )"""
        sell_trades = [t for t in self._trade_history if t.side == "SELL"]

        # 메모리에 없으면 DB 재시도
        if not sell_trades:
            self._load_trade_history_from_db()
            sell_trades = [t for t in self._trade_history if t.side == "SELL"]

        if not sell_trades:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "avg_win_pct": 0.0,
                "avg_loss_pct": 0.0,
                "profit_factor": 0.0,
                "sharpe_ratio": 0.0,
                "expectancy": 0.0,
                "max_consec_wins": 0,
                "max_consec_losses": 0,
                "message": "거래 내역 없음",
            }

        # ✅ FIX: 테이블명 오류 제거 — trade_history에서 이미 로드됨
        # (구 코드의 fallback: "SELECT profit_rate FROM trades" → 존재하지 않는 테이블)
        returns = [t.profit_rate for t in sell_trades]  # % 단위

        wins   = [r for r in returns if r > 0]
        losses = [r for r in returns if r < 0]

        win_rate      = len(wins) / len(returns) * 100 if returns else 0
        avg_win       = float(np.mean(wins))   if wins   else 0.0
        avg_loss      = float(np.mean(losses)) if losses else 0.0
        profit_factor = (
            sum(wins) / abs(sum(losses))
            if losses and wins
            else (float("inf") if wins else 0.0)
        )

        # 샤프 비율 (returns가 % 단위이므로 /100으로 소수 변환)
        returns_arr = np.array(returns) / 100
        sharpe = (
            returns_arr.mean() / returns_arr.std() * np.sqrt(365)
            if returns_arr.std() > 0
            else 0.0
        )

        # 연속 승/패
        max_cw = max_cl = cw = cl = 0
        for r in returns:
            if r > 0:
                cw += 1; cl = 0
                max_cw = max(max_cw, cw)
            else:
                cl += 1; cw = 0
                max_cl = max(max_cl, cl)

        return {
            "total_trades":      len(returns),
            "win_rate":          win_rate,
            "avg_win_pct":       avg_win,
            "avg_loss_pct":      avg_loss,
            "profit_factor":     profit_factor,
            "sharpe_ratio":      sharpe,
            "expectancy":        (
                win_rate / 100 * avg_win
                - (1 - win_rate / 100) * abs(avg_loss)
            ),
            "max_consec_wins":   max_cw,
            "max_consec_losses": max_cl,
            "total_fees_krw":    sum(t.fee for t in self._trade_history),
        }

    # ── 프로퍼티 / 유틸 ─────────────────────────────────────────
    @property
    def open_positions(self) -> Dict[str, Position]:
        return self._positions.copy()

    @property
    def position_count(self) -> int:
        return len(self._positions)

    def is_position_open(self, market: str) -> bool:
        return market in self._positions

    def get_position(self, market: str) -> Optional[Position]:
        return self._positions.get(market)

    def get_trade_history(self, limit: int = 50) -> List[TradeRecord]:
        return self._trade_history[-limit:]

    def to_dict(self) -> Dict:
        positions_data = {}
        for market, pos in self._positions.items():
            positions_data[market] = {
                "entry_price":       pos.entry_price,
                "current_price":     pos.current_price,
                "volume":            pos.volume,
                "unrealized_pnl_pct": round(pos.unrealized_pnl_pct, 2),
                "hold_hours":        round(pos.hold_hours, 1),
                "strategy":          pos.strategy,
            }
        return positions_data