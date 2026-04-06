"""
APEX BOT - 포트폴리오 관리자
보유 포지션 추적 + 성과 계산 + 드로다운 모니터링
"""
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
    """보유 포지션"""
    market: str
    entry_price: float
    volume: float
    amount_krw: float    # 투자 금액
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
    """거래 기록"""
    market: str
    side: str      # "BUY" / "SELL"
    price: float
    volume: float
    amount_krw: float
    fee: float
    timestamp: float = field(default_factory=time.time)
    strategy: str = ""
    reason: str = ""
    profit_rate: float = 0.0  # 매도시 수익률


class PortfolioManager:
    """
    포트폴리오 상태 중앙 관리

    - 포지션 추가/제거
    - 실시간 수익률 추적
    - 드로다운 계산
    - 일일/월간 성과
    - 거래 히스토리
    """

    def __init__(self):
        self.settings = get_settings()
        self._positions: Dict[str, Position] = {}
        self._trade_history: List[TradeRecord] = []
        self._load_trade_history_from_db()  # DB 복구
        self._initial_capital: float = 0.0
        self._peak_portfolio_value: float = 0.0
        self._daily_start_value: float = 0.0
        self._daily_start_time: float = time.time()

    def set_initial_capital(self, capital: float):
        self._initial_capital = capital
        self._peak_portfolio_value = capital
        self._daily_start_value = capital

    # ── 포지션 관리 ───────────────────────────────────────────────
    def open_position(self, market: str, entry_price: float, volume: float,
                      amount_krw: float, strategy: str = "",
                      stop_loss: float = 0.0, take_profit: float = 0.0):
        """포지션 열기"""
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
            f"📂 포지션 오픈 | {market} | {entry_price:,.0f} × {volume:.6f} | "
            f"₩{amount_krw:,.0f} | {strategy}"
        )
        return position

    def close_position(self, market: str, exit_price: float, fee: float,
                       reason: str = "") -> Optional[Tuple[float, float]]:
        """포지션 닫기 → (수익금, 수익률) 반환"""
        position = self._positions.get(market)
        if not position:
            logger.warning(f"포지션 없음: {market}")
            return None

        profit_rate = calculate_profit_rate(
            position.entry_price, exit_price,
            self.settings.trading.fee_rate
        ) * 100

        proceeds = exit_price * position.volume - fee

        # 거래 기록
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
            f"📁 포지션 종료 | {market} | 수익률={format_percent(profit_rate)} | "
            f"사유={reason} | 보유={position.hold_hours:.1f}h"
        )
        return proceeds, profit_rate

    def update_prices(self, price_map: Dict[str, float]):
        """현재가 일괄 업데이트"""
        for market, price in price_map.items():
            if market in self._positions:
                pos = self._positions[market]
                pos.current_price = price
                if price > pos.peak_price:
                    pos.peak_price = price

    # ── 드로다운 / 위험 지표 ─────────────────────────────────────
    def get_total_value(self, krw_balance: float) -> float:
        """총 포트폴리오 가치 (KRW + 코인 현재가)"""
        coin_value = sum(pos.current_value for pos in self._positions.values())
        return krw_balance + coin_value

    def get_current_drawdown(self, total_value: float) -> float:
        """현재 드로다운 (%)"""
        if self._peak_portfolio_value <= 0:
            return 0.0
        if total_value > self._peak_portfolio_value:
            self._peak_portfolio_value = total_value
        return (self._peak_portfolio_value - total_value) / self._peak_portfolio_value * 100

    def get_daily_pnl(self, current_value: float) -> float:
        """일일 손익 (%)"""
        # 자정 기준 초기화
        now = time.time()
        if now - self._daily_start_time > 86400:
            self._daily_start_value = current_value
            self._daily_start_time = now
        if self._daily_start_value <= 0:
            return 0.0
        return (current_value - self._daily_start_value) / self._daily_start_value * 100

    # ── 성과 통계 ─────────────────────────────────────────────────
    def _load_trade_history_from_db(self):
        """봇 재시작 시 DB에서 거래 내역 복구"""
        try:
            import sqlite3 as _sq
            conn = _sq.connect("database/apex_bot.db")
            cur  = conn.cursor()
            cur.execute("""
                SELECT timestamp, market, side, price, volume,
                       amount_krw, fee, profit_rate, strategy, reason
                FROM trade_history
                ORDER BY id ASC
            """)
            rows = cur.fetchall()
            conn.close()
            loaded = 0
            for row in rows:
                # 이미 메모리에 있으면 스킵 (중복 방지)
                ts = row[0]
                already = any(
                    getattr(t, "timestamp", None) == ts
                    for t in self._trade_history
                )
                if already:
                    continue
                # TradeRecord 또는 유사 객체 생성
                from types import SimpleNamespace
                t = SimpleNamespace(
                    timestamp=row[0], market=row[1],  side=row[2],
                    price=row[3],     volume=row[4],  amount_krw=row[5],
                    fee=row[6],       profit_rate=row[7] or 0.0,
                    strategy=row[8],  reason=row[9],
                )
                self._trade_history.append(t)
                loaded += 1
            if loaded:
                from loguru import logger
                logger.info(f"📂 거래 내역 DB 복구: {loaded}건 로드")
        except Exception as e:
            from loguru import logger
            logger.warning(f"거래 내역 DB 로드 실패: {e}")

    def get_statistics(self) -> Dict:
        """거래 성과 통계"""
        sell_trades = [t for t in self._trade_history if t.side == "SELL"]
        if not sell_trades:
            # DB 재시도 (혹시 로드 안됐을 경우)
            self._load_trade_history_from_db()
            sell_trades = [t for t in self._trade_history if t.side == "SELL"]
        if not sell_trades:
            return {
                "total_trades": 0, "win_rate": 0.0,
                "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
                "profit_factor": 0.0, "sharpe_ratio": 0.0,
                "expectancy": 0.0, "max_consec_wins": 0,
                "max_consec_losses": 0, "message": "거래 내역 없음"
            }

        returns = [t.profit_rate for t in sell_trades]
        # ── DB 보완: 메모리 sell_trades가 비어있으면 DB에서 로드 ──
        if not returns:
            try:
                import sqlite3, os as _os
                _db = "database/apex_bot.db"
                if _os.path.exists(_db):
                    _conn = sqlite3.connect(_db)
                    _cur  = _conn.cursor()
                    # trades 테이블에서 profit_rate 컬럼 읽기
                    _cur.execute(
                        "SELECT profit_rate FROM trades "
                        "WHERE profit_rate IS NOT NULL AND profit_rate != 0 "
                        "ORDER BY id DESC LIMIT 500"
                    )
                    _rows = _cur.fetchall()
                    _conn.close()
                    if _rows:
                        returns = [float(r[0]) for r in _rows]
            except Exception:
                pass
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r < 0]

        win_rate = len(wins) / len(returns) * 100 if returns else 0
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        profit_factor = (
            sum(wins) / abs(sum(losses)) if losses else float("inf")
        ) if wins else 0

        # 샤프 비율
        returns_arr = np.array(returns) / 100
        sharpe = (
            returns_arr.mean() / returns_arr.std() * np.sqrt(365)
            if returns_arr.std() > 0 else 0
        )

        # 연속 승/패
        max_consec_wins = max_consec_losses = 0
        curr_w = curr_l = 0
        for r in returns:
            if r > 0:
                curr_w += 1; curr_l = 0
                max_consec_wins = max(max_consec_wins, curr_w)
            else:
                curr_l += 1; curr_w = 0
                max_consec_losses = max(max_consec_losses, curr_l)

        return {
            "total_trades": len(returns),
            "win_rate": win_rate,
            "avg_win_pct": avg_win,
            "avg_loss_pct": avg_loss,
            "profit_factor": profit_factor,
            "sharpe_ratio": sharpe,
            "expectancy": (win_rate/100 * avg_win - (1-win_rate/100) * abs(avg_loss)),
            "max_consec_wins": max_consec_wins,
            "max_consec_losses": max_consec_losses,
            "total_fees_krw": sum(t.fee for t in self._trade_history),
        }

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
        """대시보드용 직렬화"""
        positions_data = {}
        for market, pos in self._positions.items():
            positions_data[market] = {
                "entry_price": pos.entry_price,
                "current_price": pos.current_price,
                "volume": pos.volume,
                "unrealized_pnl_pct": round(pos.unrealized_pnl_pct, 2),
                "hold_hours": round(pos.hold_hours, 1),
                "strategy": pos.strategy,
            }
        return positions_data
