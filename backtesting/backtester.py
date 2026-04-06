"""
APEX BOT Backtester v3.0
- Upbit 과거 데이터 자동 수집
- 8개 전략 백테스트
- Walk-Forward Analysis
- 수수료·슬리피지 현실적 반영
- JSON + HTML 리포트 자동 생성
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from loguru import logger

from config.settings import get_settings
from backtesting.data_loader import fetch_ohlcv
from backtesting.signal_generator import get_signals, STRATEGIES


@dataclass
class Trade:
    """개별 거래 기록"""
    market:       str
    entry_time:   datetime
    exit_time:    Optional[datetime]
    entry_price:  float
    exit_price:   float = 0.0
    volume:       float = 0.0
    side:         str   = "buy"
    fee_rate:     float = 0.001
    stop_loss:    float = 0.0
    take_profit:  float = 0.0
    reason_entry: str   = ""
    reason_exit:  str   = ""
    status:       str   = "open"

    @property
    def gross_return(self) -> float:
        if self.exit_price == 0:
            return 0.0
        return (self.exit_price - self.entry_price) / self.entry_price

    @property
    def net_return(self) -> float:
        return self.gross_return - self.fee_rate * 2

    @property
    def profit_krw(self) -> float:
        return self.net_return * self.entry_price * self.volume

    @property
    def is_win(self) -> bool:
        return self.net_return > 0

    @property
    def duration_hours(self) -> float:
        if self.exit_time is None:
            return 0.0
        return (self.exit_time - self.entry_time).total_seconds() / 3600


@dataclass
class BacktestResult:
    """백테스트 결과"""
    market:          str
    strategy:        str
    start_date:      str
    end_date:        str
    initial_capital: float
    final_capital:   float
    trades:          List[Trade]      = field(default_factory=list)
    equity_curve:    pd.Series        = field(default_factory=pd.Series)
    # 성과 지표
    total_return:    float = 0.0
    annual_return:   float = 0.0
    sharpe_ratio:    float = 0.0
    sortino_ratio:   float = 0.0
    max_drawdown:    float = 0.0
    calmar_ratio:    float = 0.0
    win_rate:        float = 0.0
    profit_factor:   float = 0.0
    avg_win:         float = 0.0
    avg_loss:        float = 0.0
    expectancy:      float = 0.0
    total_trades:    int   = 0
    total_fees:      float = 0.0

    def summary(self) -> dict:
        return {
            "market":       self.market,
            "strategy":     self.strategy,
            "기간":         f"{self.start_date} ~ {self.end_date}",
            "초기자본":     f"₩{self.initial_capital:,.0f}",
            "최종자본":     f"₩{self.final_capital:,.0f}",
            "총수익률":     f"{self.total_return:.2f}%",
            "연환산수익률": f"{self.annual_return:.2f}%",
            "샤프비율":     f"{self.sharpe_ratio:.3f}",
            "소르티노":     f"{self.sortino_ratio:.3f}",
            "최대낙폭":     f"{self.max_drawdown:.2f}%",
            "칼마비율":     f"{self.calmar_ratio:.3f}",
            "승률":         f"{self.win_rate:.1f}%",
            "손익비율":     f"{self.profit_factor:.2f}",
            "기댓값":       f"{self.expectancy:.4f}",
            "총거래수":     self.total_trades,
            "총수수료":     f"₩{self.total_fees:,.0f}",
            "평균수익":     f"{self.avg_win:.2f}%",
            "평균손실":     f"{self.avg_loss:.2f}%",
        }

    def print_summary(self):
        s = self.summary()
        print("\n" + "="*55)
        print(f"  📊 백테스트 결과: {s['strategy']} / {s['market']}")
        print("="*55)
        for k, v in s.items():
            if k not in ("market", "strategy"):
                print(f"  {k:<12}: {v}")
        print("="*55)


class Backtester:
    """
    APEX BOT 메인 백테스터
    - Upbit 과거 데이터 자동 수집
    - 수수료(0.05%) + 슬리피지(0.1%) 현실 반영
    - 전략 8개 일괄 실행 지원
    - Walk-Forward 분석 내장
    """

    def __init__(
        self,
        initial_capital: float     = 1_000_000.0,
        fee_rate:        float     = 0.0005,
        slippage:        float     = 0.001,
        stop_loss_pct:   float     = 0.05,
        take_profit_pct: float     = 0.10,
        position_size:   float     = 0.20,
        max_positions:   int       = 5,
    ):
        self.initial_capital = initial_capital
        self.fee_rate        = fee_rate
        self.slippage        = slippage
        self.stop_loss_pct   = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.position_size   = position_size
        self.max_positions   = max_positions

    # ── 단일 전략 백테스트 ──────────────────────────────────────────────────
    def run(
        self,
        df:       pd.DataFrame,
        strategy: str,
        market:   str = "KRW-BTC",
        **signal_kwargs,
    ) -> BacktestResult:
        """
        단일 전략, 단일 코인 백테스트 실행

        Args:
            df:       OHLCV DataFrame (fetch_ohlcv_sync 결과)
            strategy: 전략 이름 (STRATEGIES 딕셔너리 키)
            market:   코인 마켓 코드
        """
        if df.empty:
            logger.error("빈 DataFrame – 백테스트 불가")
            return self._empty_result(market, strategy)

        signals = get_signals(strategy, df, **signal_kwargs)
        return self._simulate(df, signals, strategy, market)

    # ── 전략 전체 일괄 백테스트 ────────────────────────────────────────────
    async def run_all_strategies(
        self,
        market:   str = "KRW-BTC",
        interval: str = "1d",
        days:     int = 365,
    ) -> Dict[str, BacktestResult]:
        """8개 전략 전체를 한 번에 백테스트"""
        logger.info(f"[Backtester] {market} {interval} {days}일 데이터 수집 중...")
        df = await fetch_ohlcv(market, interval, days)
        if df.empty:
            logger.error("데이터 로드 실패")
            return {}

        results = {}
        for name in STRATEGIES:
            logger.info(f"  → {name} 백테스트 중...")
            results[name] = self.run(df, name, market)

        return results

    # ── Walk-Forward Analysis ───────────────────────────────────────────────
    def walk_forward(
        self,
        df:          pd.DataFrame,
        strategy:    str,
        market:      str   = "KRW-BTC",
        n_splits:    int   = 5,
        test_ratio:  float = 0.2,
    ) -> List[BacktestResult]:
        """
        Walk-Forward Analysis
        전체 데이터를 n_splits개 구간으로 나누어
        각 구간의 앞 (1-test_ratio)를 학습, 뒤 test_ratio를 테스트합니다.
        """
        results = []
        total   = len(df)
        step    = total // n_splits

        for i in range(n_splits):
            end_idx   = step * (i + 1)
            start_idx = max(0, end_idx - step * 2)  # 2 구간을 훈련
            test_start = int(end_idx * (1 - test_ratio))

            train_df = df.iloc[start_idx:test_start]
            test_df  = df.iloc[test_start:end_idx]

            if len(test_df) < 20:
                continue

            result = self.run(test_df, strategy, market)
            result.start_date = str(test_df.index[0].date())
            result.end_date   = str(test_df.index[-1].date())
            results.append(result)
            logger.info(
                f"  [WF {i+1}/{n_splits}] {result.start_date}~{result.end_date} "
                f"수익률={result.total_return:.1f}% 거래수={result.total_trades}"
            )

        return results

    # ── 내부 시뮬레이터 ────────────────────────────────────────────────────
    def _simulate(
        self,
        df:       pd.DataFrame,
        signals:  pd.Series,
        strategy: str,
        market:   str,
    ) -> BacktestResult:
        capital   = self.initial_capital
        position  = None
        trades    = []
        equity    = []
        total_fee = 0.0

        for i in range(len(df)):
            row   = df.iloc[i]
            sig   = int(signals.iloc[i]) if i < len(signals) else 0
            price = float(row["close"])
            ts    = df.index[i]

            # 보유 중 청산 조건 체크
            if position is not None:
                pnl          = (price - position["entry"]) / position["entry"]
                should_exit  = False
                exit_reason  = ""

                if pnl <= -self.stop_loss_pct:
                    should_exit = True
                    exit_reason = "STOP_LOSS"
                elif pnl >= self.take_profit_pct:
                    should_exit = True
                    exit_reason = "TAKE_PROFIT"
                elif sig == -1:
                    should_exit = True
                    exit_reason = "SIGNAL_SELL"

                if should_exit:
                    exit_price = price * (1 - self.slippage)
                    proceeds   = position["volume"] * exit_price
                    fee        = proceeds * self.fee_rate
                    profit_krw = proceeds - fee - position["cost"]
                    capital   += proceeds - fee
                    total_fee += fee + position["entry_fee"]

                    trades.append(Trade(
                        market       = market,
                        entry_time   = position["time"],
                        exit_time    = ts,
                        entry_price  = position["entry"],
                        exit_price   = exit_price,
                        volume       = position["volume"],
                        fee_rate     = self.fee_rate,
                        stop_loss    = position["entry"] * (1 - self.stop_loss_pct),
                        take_profit  = position["entry"] * (1 + self.take_profit_pct),
                        reason_entry = "SIGNAL_BUY",
                        reason_exit  = exit_reason,
                        status       = "closed",
                    ))
                    position = None

            # 매수 신호
            if sig == 1 and position is None:
                buy_price = price * (1 + self.slippage)
                amount    = capital * self.position_size
                fee       = amount * self.fee_rate
                cost      = amount + fee
                if cost <= capital:
                    volume    = amount / buy_price
                    capital  -= cost
                    position  = {
                        "entry":     buy_price,
                        "volume":    volume,
                        "cost":      cost,
                        "time":      ts,
                        "entry_fee": fee,
                    }

            # 자산 곡선 기록
            pos_value = (position["volume"] * price) if position else 0.0
            equity.append(capital + pos_value)

        # 미청산 포지션 강제 청산
        if position is not None:
            last_price = float(df.iloc[-1]["close"])
            exit_price = last_price * (1 - self.slippage)
            proceeds   = position["volume"] * exit_price
            fee        = proceeds * self.fee_rate
            capital   += proceeds - fee
            total_fee += fee
            trades.append(Trade(
                market       = market,
                entry_time   = position["time"],
                exit_time    = df.index[-1],
                entry_price  = position["entry"],
                exit_price   = exit_price,
                volume       = position["volume"],
                fee_rate     = self.fee_rate,
                reason_entry = "SIGNAL_BUY",
                reason_exit  = "END_OF_DATA",
                status       = "closed",
            ))

        equity_series = pd.Series(equity, index=df.index)
        result = BacktestResult(
            market          = market,
            strategy        = strategy,
            start_date      = str(df.index[0].date()),
            end_date        = str(df.index[-1].date()),
            initial_capital = self.initial_capital,
            final_capital   = capital,
            trades          = trades,
            equity_curve    = equity_series,
            total_fees      = total_fee,
        )
        return self._calc_metrics(result)

    def _calc_metrics(self, r: BacktestResult) -> BacktestResult:
        """성과 지표 계산"""
        r.total_trades = len(r.trades)

        if r.total_trades == 0:
            return r

        wins   = [t for t in r.trades if t.is_win]
        losses = [t for t in r.trades if not t.is_win]

        r.win_rate = len(wins) / r.total_trades * 100

        gross_profit = sum(t.profit_krw for t in wins)
        gross_loss   = abs(sum(t.profit_krw for t in losses))
        r.profit_factor = gross_profit / gross_loss if gross_loss > 0 else 99.0

        r.avg_win  = np.mean([t.net_return * 100 for t in wins])  if wins   else 0.0
        r.avg_loss = np.mean([t.net_return * 100 for t in losses]) if losses else 0.0

        r.expectancy    = (r.win_rate / 100 * r.avg_win) + ((1 - r.win_rate / 100) * r.avg_loss)
        r.total_return  = (r.final_capital - r.initial_capital) / r.initial_capital * 100

        # 연환산 수익률
        if not r.equity_curve.empty:
            days           = (r.equity_curve.index[-1] - r.equity_curve.index[0]).days
            years          = max(days / 365.0, 1 / 365)
            r.annual_return = ((r.final_capital / r.initial_capital) ** (1 / years) - 1) * 100

        # Sharpe / Sortino
        if not r.equity_curve.empty and len(r.equity_curve) > 1:
            daily_ret  = r.equity_curve.pct_change().dropna()
            if daily_ret.std() > 0:
                r.sharpe_ratio = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252)
            down_std = daily_ret[daily_ret < 0].std()
            if down_std > 0:
                r.sortino_ratio = (daily_ret.mean() / down_std) * np.sqrt(252)

        # Max Drawdown
        if not r.equity_curve.empty:
            roll_max  = r.equity_curve.cummax()
            drawdown  = (r.equity_curve - roll_max) / roll_max * 100
            r.max_drawdown = abs(drawdown.min())

        # Calmar
        if r.max_drawdown > 0:
            r.calmar_ratio = r.annual_return / r.max_drawdown

        return r

    def _empty_result(self, market: str, strategy: str) -> BacktestResult:
        return BacktestResult(
            market=market, strategy=strategy,
            start_date="N/A", end_date="N/A",
            initial_capital=self.initial_capital,
            final_capital=self.initial_capital,
        )
