# -*- coding: utf-8 -*-
"""
real_backtester.py
실제 전략 클래스(strategies/)를 직접 사용하는 백테스터
- 기존 backtesting/signal_generator.py 완전 우회
- StrategySignal.stop_loss / take_profit 실제 사용
- 슬리피지 + 수수료 정확 반영
"""
import sys, asyncio
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from pathlib import Path
sys.path.insert(0, '.')

# ── 전략 임포트 ───────────────────────────────────────────
from strategies.mean_reversion.bollinger_squeeze  import BollingerSqueezeStrategy
from strategies.market_structure.order_block       import OrderBlockStrategy
from strategies.momentum.macd_cross                import MACDCrossStrategy
from strategies.momentum.rsi_divergence            import RSIDivergenceStrategy
from strategies.momentum.supertrend                import SupertrendStrategy
from strategies.mean_reversion.vwap_reversion      import VWAPReversionStrategy
from strategies.volatility.vol_breakout            import VolBreakoutStrategy
from strategies.base_strategy                      import SignalType

REAL_STRATEGIES = {
    "BollingerSqueeze": BollingerSqueezeStrategy,
    "OrderBlock":       OrderBlockStrategy,
    "MACD_Cross":       MACDCrossStrategy,
    "RSI_Divergence":   RSIDivergenceStrategy,
    "Supertrend":       SupertrendStrategy,
    "VWAP_Reversion":   VWAPReversionStrategy,
    "Vol_Breakout":     VolBreakoutStrategy,
}

# ── 결과 데이터클래스 ─────────────────────────────────────
@dataclass
class RealBacktestResult:
    strategy:      str
    market:        str
    total_trades:  int   = 0
    wins:          int   = 0
    losses:        int   = 0
    win_rate:      float = 0.0
    total_pnl:     float = 0.0   # % 합계
    avg_pnl:       float = 0.0   # 거래당 평균 %
    expectancy:    float = 0.0   # 기댓값 (수수료 차감)
    sharpe_ratio:  float = 0.0
    max_drawdown:  float = 0.0
    avg_hold_bars: float = 0.0
    trades:        List  = field(default_factory=list)

# ── 핵심 백테스터 ─────────────────────────────────────────
class RealBacktester:
    FEE      = 0.0005   # 매수 0.05% + 매도 0.05% = 0.1%
    SLIP     = 0.001    # 슬리피지 0.1%
    MAX_HOLD = 20       # 최대 보유 봉수 (일봉 기준 20일)

    def run(self, df: pd.DataFrame, strategy_name: str,
            market: str) -> RealBacktestResult:
        result = RealBacktestResult(strategy=strategy_name, market=market)
        klass  = REAL_STRATEGIES[strategy_name]
        obj    = klass()

        capital    = 1_000_000.0   # 기준 자본 (비율 계산용)
        equity     = capital
        peak       = capital
        max_dd     = 0.0
        pnl_series = []
        in_pos     = False
        entry_price = sl = tp = 0.0
        entry_bar  = 0

        for i in range(50, len(df)):
            window = df.iloc[:i].copy()
            bar    = df.iloc[i]

            # ── 포지션 청산 체크 ──────────────────────────
            if in_pos:
                cur = float(bar["close"])
                held = i - entry_bar

                # SL/TP 또는 최대 보유기간 도달
                exit_price = None
                exit_reason = None

                if cur <= sl:
                    exit_price  = sl * (1 - self.SLIP)
                    exit_reason = "SL"
                elif cur >= tp:
                    exit_price  = tp * (1 - self.SLIP)
                    exit_reason = "TP"
                elif held >= self.MAX_HOLD:
                    exit_price  = cur * (1 - self.SLIP)
                    exit_reason = "MAX_HOLD"

                if exit_price:
                    gross = (exit_price - entry_price) / entry_price * 100
                    net   = gross - (self.FEE + self.SLIP) * 2 * 100
                    result.trades.append({
                        "entry_bar": entry_bar, "exit_bar": i,
                        "entry": entry_price, "exit": exit_price,
                        "pnl": net, "reason": exit_reason,
                        "hold_bars": held
                    })
                    pnl_series.append(net)
                    equity *= (1 + net / 100)
                    peak    = max(peak, equity)
                    dd      = (peak - equity) / peak * 100
                    max_dd  = max(max_dd, dd)
                    in_pos  = False

            # ── 신규 진입 체크 ────────────────────────────
            if not in_pos:
                try:
                    sig = obj.generate_signal(window, market)
                except Exception:
                    sig = None

                if sig is not None and sig.signal == SignalType.BUY:
                    raw_entry = float(bar["open"])   # 다음 봉 시가에 진입
                    entry_price = raw_entry * (1 + self.SLIP)
                    # 전략이 제공한 SL/TP 사용, 없으면 ATR 기반 기본값
                    sl = float(sig.stop_loss)  if sig.stop_loss  and sig.stop_loss  > 0 else entry_price * 0.978
                    tp = float(sig.take_profit) if sig.take_profit and sig.take_profit > 0 else entry_price * 1.045
                    # 비정상값 방어
                    if sl >= entry_price or tp <= entry_price:
                        sl = entry_price * 0.978
                        tp = entry_price * 1.045
                    in_pos    = True
                    entry_bar = i

        # 미청산 포지션 강제 청산
        if in_pos and len(df) > entry_bar:
            exit_price = float(df.iloc[-1]["close"]) * (1 - self.SLIP)
            gross = (exit_price - entry_price) / entry_price * 100
            net   = gross - (self.FEE + self.SLIP) * 2 * 100
            result.trades.append({
                "entry_bar": entry_bar, "exit_bar": len(df)-1,
                "pnl": net, "reason": "END"
            })
            pnl_series.append(net)

        # ── 통계 계산 ─────────────────────────────────────
        result.total_trades = len(pnl_series)
        if result.total_trades == 0:
            return result

        wins   = [p for p in pnl_series if p > 0]
        losses = [p for p in pnl_series if p <= 0]
        result.wins       = len(wins)
        result.losses     = len(losses)
        result.win_rate   = len(wins) / result.total_trades * 100
        result.total_pnl  = sum(pnl_series)
        result.avg_pnl    = result.total_pnl / result.total_trades
        result.max_drawdown = max_dd

        avg_win  = np.mean(wins)   if wins   else 0
        avg_loss = abs(np.mean(losses)) if losses else 1e-8
        result.expectancy = (result.win_rate/100 * avg_win
                           - (1 - result.win_rate/100) * avg_loss)

        if len(pnl_series) >= 2:
            arr = np.array(pnl_series)
            result.sharpe_ratio = (arr.mean() / (arr.std() + 1e-8)
                                   * np.sqrt(252 / self.MAX_HOLD))

        result.avg_hold_bars = np.mean(
            [t.get("hold_bars", 0) for t in result.trades]
        )
        return result


# ── 실행 ─────────────────────────────────────────────────
async def main():
    from backtesting.data_loader import fetch_ohlcv

    MARKETS = [
        "KRW-BTC","KRW-ETH","KRW-XRP","KRW-SOL","KRW-ADA",
        "KRW-DOGE","KRW-AVAX","KRW-DOT","KRW-LINK","KRW-ATOM"
    ]
    DAYS = 365   # 1년치 데이터 (거래수 확보)
    bt   = RealBacktester()

    summary: Dict[str, Dict] = {
        s: {"wins":0,"total":0,"ev_list":[],"sh_list":[],"mdd_max":0}
        for s in REAL_STRATEGIES
    }

    print("=" * 72)
    print(f"  실제 전략 코드 기반 백테스트 | {DAYS}일 | {len(MARKETS)}개 코인")
    print("=" * 72)
    print(f"  {'코인':<10} {'전략':<20} {'승률':>6} {'기댓값':>8} "
          f"{'샤프':>7} {'MDD':>6} {'거래':>4} {'평균보유':>6}")
    print("-" * 72)

    for market in MARKETS:
        df = await fetch_ohlcv(market, "1d", DAYS)
        if df is None or len(df) < 100:
            print(f"  ⚠️  {market} 데이터 부족")
            continue
        for strat_name in REAL_STRATEGIES:
            try:
                res = bt.run(df, strat_name, market)
                if res.total_trades == 0:
                    continue
                icon = ("✅" if res.expectancy > 0 and res.win_rate >= 50
                        else "🟡" if res.expectancy > 0
                        else "❌")
                print(f"  {icon} {market:<10} {strat_name:<20} "
                      f"{res.win_rate:>5.1f}% {res.expectancy:>+8.4f} "
                      f"{res.sharpe_ratio:>+7.3f} {res.max_drawdown:>5.1f}% "
                      f"{res.total_trades:>4}회 {res.avg_hold_bars:>5.1f}봉")
                s = summary[strat_name]
                s["total"]   += res.total_trades
                s["wins"]    += res.wins
                s["ev_list"].append(res.expectancy)
                s["sh_list"].append(res.sharpe_ratio)
                s["mdd_max"]  = max(s["mdd_max"], res.max_drawdown)
            except Exception as e:
                print(f"  ⚠️  {market} {strat_name}: {e}")

    # ── 통합 결과 ─────────────────────────────────────────
    print("\n" + "=" * 72)
    print("  전략별 통합 결과 (10코인 365일 — 실제 전략 코드 기반)")
    print("=" * 72)
    candidates = []
    for strat, s in summary.items():
        if s["total"] == 0:
            continue
        wr      = s["wins"] / s["total"] * 100
        avg_ev  = np.mean(s["ev_list"])
        avg_sh  = np.mean(s["sh_list"])
        pos_cnt = sum(1 for e in s["ev_list"] if e > 0)
        verdict = ("🟢 실거래후보" if wr >= 52 and avg_ev > 0.1 and s["total"] >= 30
                   else "🟡 조건부"  if avg_ev > 0 and s["total"] >= 15
                   else "🔴 제외")
        print(f"  {verdict} {strat:<20} "
              f"승률={wr:.1f}% 기댓값={avg_ev:+.4f} "
              f"샤프={avg_sh:+.3f} MDD={s['mdd_max']:.1f}% "
              f"양수코인={pos_cnt}/{len(s['ev_list'])}개 거래={s['total']}회")
        if avg_ev > 0 and s["total"] >= 15:
            candidates.append((strat, wr, avg_ev, avg_sh, s["total"]))

    print("\n" + "=" * 72)
    print("  판정 기준")
    print("=" * 72)
    print("  🟢 실거래후보: 승률≥52% AND 기댓값>0.1% AND 거래≥30회")
    print("  🟡 조건부    : 기댓값>0 AND 거래≥15회 (추가검증 필요)")
    print("  🔴 제외      : 기댓값≤0 또는 거래<15회")

    if candidates:
        print(f"\n  → 다음 단계 후보 전략: {[c[0] for c in candidates]}")
        print("  → 후보 전략으로 signal_combiner 재구성 후 페이퍼 트레이딩")
    else:
        print("\n  → 후보 없음: 전략 파라미터 튜닝 또는 새 전략 추가 필요")

asyncio.run(main())
