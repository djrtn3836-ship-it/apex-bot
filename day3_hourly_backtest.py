# -*- coding: utf-8 -*-
"""
3일차: 1시간봉 기반 실제 백테스트
- 봇 실제 운용 타임프레임과 일치
- 365일 x 24봉/일 = 약 8,760봉
- 통계적으로 의미 있는 거래수 확보 목표 (코인당 30회+)
"""
import sys, asyncio
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict
sys.path.insert(0, '.')

from strategies.market_structure.order_block      import OrderBlockStrategy
from strategies.momentum.macd_cross               import MACDCrossStrategy
from strategies.mean_reversion.bollinger_squeeze  import BollingerSqueezeStrategy
from strategies.momentum.rsi_divergence           import RSIDivergenceStrategy
from strategies.base_strategy                     import SignalType
from backtesting.data_loader                      import fetch_ohlcv

class HourlyBacktester:
    """1시간봉 기반 백테스터 — 실제 봇 타임프레임"""
    FEE      = 0.001   # 왕복 수수료
    SLIP     = 0.001
    MAX_HOLD = 48      # 최대 보유 48시간

    def __init__(self, sl_mult=1.2, tp_mult=2.5):
        self.sl_mult = sl_mult
        self.tp_mult = tp_mult

    def _atr(self, df: pd.DataFrame, period=14) -> float:
        h = df["high"].values.astype(float)
        l = df["low"].values.astype(float)
        c = df["close"].values.astype(float)
        tr = np.maximum(h-l,
             np.maximum(abs(h-np.roll(c,1)),
                        abs(l-np.roll(c,1))))
        return float(pd.Series(tr).ewm(span=period).mean().iloc[-1])

    def run(self, df: pd.DataFrame, strategy_cls, market: str) -> Dict:
        obj    = strategy_cls()
        trades = []
        in_pos = False
        entry  = sl = tp = 0.0
        entry_bar = 0

        for i in range(100, len(df)):
            window = df.iloc[:i].copy()
            bar    = df.iloc[i]
            cur    = float(bar["close"])

            if in_pos:
                held = i - entry_bar
                ep   = None
                if cur <= sl:
                    ep = sl * (1 - self.SLIP)
                elif cur >= tp:
                    ep = tp * (1 - self.SLIP)
                elif held >= self.MAX_HOLD:
                    ep = cur * (1 - self.SLIP)
                if ep is not None:
                    pnl = (ep - entry)/entry*100 - self.FEE*100
                    trades.append({"pnl": pnl, "hold": held,
                                   "win": pnl > 0})
                    in_pos = False

            if not in_pos:
                try:
                    sig = obj.generate_signal(window, market)
                except Exception:
                    sig = None
                if sig is not None and sig.signal == SignalType.BUY:
                    atr   = self._atr(window)
                    entry = float(bar["open"]) * (1 + self.SLIP)
                    sl    = entry - atr * self.sl_mult
                    tp    = entry + atr * self.tp_mult
                    if sl >= entry or tp <= entry or atr <= 0:
                        sl = entry * (1 - 0.012)
                        tp = entry * (1 + 0.025)
                    in_pos    = True
                    entry_bar = i

        if in_pos:
            ep  = float(df.iloc[-1]["close"]) * (1 - self.SLIP)
            pnl = (ep - entry)/entry*100 - self.FEE*100
            trades.append({"pnl": pnl, "hold": i - entry_bar, "win": pnl > 0})

        if not trades:
            return {"total":0,"wr":0,"ev":0,"sh":0,"mdd":0,"avg_hold":0}

        pnl_list = [t["pnl"] for t in trades]
        wins     = [p for p in pnl_list if p > 0]
        losses   = [p for p in pnl_list if p <= 0]
        wr       = len(wins) / len(trades) * 100
        av_w     = np.mean(wins)   if wins   else 0
        av_l     = abs(np.mean(losses)) if losses else 1e-8
        ev       = wr/100*av_w - (1-wr/100)*av_l

        arr = np.array(pnl_list)
        sh  = arr.mean()/(arr.std()+1e-8) * np.sqrt(8760/self.MAX_HOLD) if len(arr)>=2 else 0

        equity = 100.0; peak = 100.0; mdd = 0.0
        for p in pnl_list:
            equity *= (1 + p/100)
            peak    = max(peak, equity)
            mdd     = max(mdd, (peak-equity)/peak*100)

        return {
            "total":    len(trades),
            "wr":       wr,
            "ev":       ev,
            "sh":       sh,
            "mdd":      mdd,
            "avg_hold": np.mean([t["hold"] for t in trades]),
        }


async def main():
    MARKETS = [
        "KRW-BTC","KRW-ETH","KRW-XRP","KRW-SOL","KRW-ADA",
        "KRW-DOGE","KRW-AVAX","KRW-DOT","KRW-LINK","KRW-ATOM"
    ]

    STRATEGIES = {
        "OrderBlock":       (OrderBlockStrategy,       1.2, 2.5),
        "MACD_Cross":       (MACDCrossStrategy,        1.5, 3.0),
        "BollingerSqueeze": (BollingerSqueezeStrategy, 1.5, 3.0),
        "RSI_Divergence":   (RSIDivergenceStrategy,    1.5, 3.0),
    }

    print("=" * 72)
    print("  1시간봉 백테스트 | 365일(8,760봉) | 10코인 | 실제 봇 타임프레임")
    print("=" * 72)
    print(f"  {'코인':<10} {'전략':<20} {'승률':>6} {'기댓값':>8} "
          f"{'샤프':>7} {'MDD':>6} {'거래':>5} {'평균보유':>7}")
    print("-" * 72)

    summary = {s: {"wins":0,"total":0,"ev_list":[],"sh_list":[]}
               for s in STRATEGIES}
    coin_map = {}

    for market in MARKETS:
        # 1시간봉 365일 데이터
        df = await fetch_ohlcv(market, "1h", 365)
        if df is None or len(df) < 200:
            print(f"  ⚠️  {market} 데이터 부족 ({len(df) if df else 0}봉)")
            continue

        print(f"\n  [{market}] 총 {len(df)}봉")
        best_ev    = -999
        best_strat = None

        for strat_name, (strat_cls, sl_m, tp_m) in STRATEGIES.items():
            try:
                bt  = HourlyBacktester(sl_mult=sl_m, tp_mult=tp_m)
                res = bt.run(df, strat_cls, market)

                if res["total"] == 0:
                    continue

                icon = ("✅" if res["ev"] > 0 and res["wr"] >= 50
                        else "🟡" if res["ev"] > 0
                        else "❌")
                stat = ("✅ 통계유의" if res["total"] >= 30
                        else "⚠️ 샘플부족" if res["total"] >= 15
                        else "❌ 샘플불충분")
                print(f"  {icon} {market:<10} {strat_name:<20} "
                      f"{res['wr']:>5.1f}% {res['ev']:>+8.4f} "
                      f"{res['sh']:>+7.3f} {res['mdd']:>5.1f}% "
                      f"{res['total']:>5}회 {res['avg_hold']:>5.1f}h  {stat}")

                s = summary[strat_name]
                s["total"]   += res["total"]
                s["wins"]    += int(res["wr"] * res["total"] / 100)
                s["ev_list"].append(res["ev"])
                s["sh_list"].append(res["sh"])

                if res["total"] >= 15 and res["ev"] > best_ev:
                    best_ev    = res["ev"]
                    best_strat = strat_name
            except Exception as e:
                print(f"  ⚠️  {market} {strat_name}: {e}")

        if best_strat and best_ev > 0:
            coin_map[market] = {"strategy": best_strat, "ev": best_ev}

    # ── 전략별 통합 결과 ──────────────────────────────────
    print("\n" + "=" * 72)
    print("  전략별 통합 결과 (1시간봉 기준)")
    print("=" * 72)
    qualified = []
    for strat_name, s in summary.items():
        if s["total"] == 0:
            continue
        wr     = s["wins"] / s["total"] * 100
        avg_ev = np.mean(s["ev_list"])
        avg_sh = np.mean(s["sh_list"])
        pos    = sum(1 for e in s["ev_list"] if e > 0)
        stat   = "✅ 통계유의" if s["total"] >= 30 else "⚠️ 부족"
        verdict = ("🟢 실거래후보" if wr >= 50 and avg_ev > 0 and s["total"] >= 30
                   else "🟡 조건부"  if avg_ev > 0 and s["total"] >= 20
                   else "🔴 제외")
        print(f"  {verdict} {strat_name:<20} 승률={wr:.1f}% "
              f"기댓값={avg_ev:+.4f} 샤프={avg_sh:+.3f} "
              f"양수코인={pos}/{len(s['ev_list'])}개 거래={s['total']}회  {stat}")
        if avg_ev > 0 and s["total"] >= 20:
            qualified.append((strat_name, wr, avg_ev, s["total"]))

    # ── 코인별 최종 매핑 ──────────────────────────────────
    print("\n" + "=" * 72)
    print("  코인별 최적 전략 매핑 (1시간봉 기반)")
    print("=" * 72)
    if coin_map:
        for market, cfg in coin_map.items():
            print(f"  ✅ {market:<12} → {cfg['strategy']:<20} "
                  f"기댓값={cfg['ev']:+.4f}%")
        excluded = [m for m in MARKETS if m not in coin_map]
        if excluded:
            print(f"\n  ❌ 제외: {excluded}")
    else:
        print("  ⚠️  유효한 코인-전략 매핑 없음")

    print(f"""
  ━━━ 판단 기준 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  거래수 ≥ 30회  : 통계적으로 유의미
  거래수 15~29회 : 참고만 가능
  거래수 < 15회  : 신뢰 불가

  🟢 실거래후보: 승률≥50% AND 기댓값>0 AND 거래≥30회
  → 이 기준을 통과한 전략만 실제 봇에 적용합니다
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")

asyncio.run(main())
