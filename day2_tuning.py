# -*- coding: utf-8 -*-
"""
2일차: 코인별 최적 전략 매핑 + SL/TP 파라미터 튜닝
- OrderBlock 전략의 SL 배수를 0.8~2.0x ATR로 변경하며 최적값 탐색
- 코인별 최적 (전략, SL배수) 조합 확정
"""
import sys, asyncio
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict, Tuple
sys.path.insert(0, '.')

from strategies.market_structure.order_block import OrderBlockStrategy
from strategies.momentum.macd_cross          import MACDCrossStrategy
from strategies.mean_reversion.bollinger_squeeze import BollingerSqueezeStrategy
from strategies.base_strategy                import SignalType
from backtesting.data_loader                 import fetch_ohlcv

# ── 파라미터 튜닝 백테스터 ────────────────────────────────
class TuningBacktester:
    def __init__(self, sl_atr_mult: float = 1.5,
                 tp_atr_mult: float = 3.0,
                 max_hold: int = 15):
        self.sl_mult  = sl_atr_mult
        self.tp_mult  = tp_atr_mult
        self.max_hold = max_hold
        self.fee      = 0.001   # 왕복 수수료 0.1%
        self.slip     = 0.001   # 슬리피지

    def _atr(self, df: pd.DataFrame, period: int = 14) -> float:
        h, l, c = df["high"].values, df["low"].values, df["close"].values
        tr = np.maximum(h - l,
             np.maximum(abs(h - np.roll(c,1)),
                        abs(l - np.roll(c,1))))
        return float(pd.Series(tr).ewm(span=period).mean().iloc[-1])

    def run(self, df: pd.DataFrame, strategy_cls,
            market: str) -> Dict:
        obj     = strategy_cls()
        trades  = []
        in_pos  = False
        entry   = sl = tp = 0.0
        entry_bar = 0

        for i in range(60, len(df)):
            window = df.iloc[:i].copy()
            bar    = df.iloc[i]
            cur    = float(bar["close"])

            if in_pos:
                held = i - entry_bar
                ep   = None
                if cur <= sl:
                    ep = sl * (1 - self.slip)
                elif cur >= tp:
                    ep = tp * (1 - self.slip)
                elif held >= self.max_hold:
                    ep = cur * (1 - self.slip)
                if ep is not None:
                    pnl = (ep - entry) / entry * 100 - self.fee * 100
                    trades.append(pnl)
                    in_pos = False

            if not in_pos:
                try:
                    sig = obj.generate_signal(window, market)
                except Exception:
                    sig = None
                if sig is not None and sig.signal == SignalType.BUY:
                    atr   = self._atr(window)
                    entry = float(bar["open"]) * (1 + self.slip)
                    sl    = entry - atr * self.sl_mult
                    tp    = entry + atr * self.tp_mult
                    in_pos    = True
                    entry_bar = i

        if in_pos:
            ep  = float(df.iloc[-1]["close"]) * (1 - self.slip)
            pnl = (ep - entry) / entry * 100 - self.fee * 100
            trades.append(pnl)

        if not trades:
            return {"total":0, "wr":0, "ev":0, "sh":0, "mdd":0}

        wins = [p for p in trades if p > 0]
        wr   = len(wins) / len(trades) * 100
        av_w = np.mean(wins) if wins else 0
        loss = [p for p in trades if p <= 0]
        av_l = abs(np.mean(loss)) if loss else 1e-8
        ev   = wr/100 * av_w - (1 - wr/100) * av_l

        arr = np.array(trades)
        sh  = arr.mean() / (arr.std() + 1e-8) * np.sqrt(252/self.max_hold) if len(arr) >= 2 else 0

        # MDD
        equity = 100.0
        peak   = 100.0
        mdd    = 0.0
        for p in trades:
            equity *= (1 + p/100)
            peak    = max(peak, equity)
            mdd     = max(mdd, (peak - equity)/peak*100)

        return {"total": len(trades), "wr": wr, "ev": ev, "sh": sh, "mdd": mdd}


async def main():
    MARKETS = [
        "KRW-BTC","KRW-ETH","KRW-XRP","KRW-SOL","KRW-ADA",
        "KRW-DOGE","KRW-AVAX","KRW-DOT","KRW-LINK","KRW-ATOM"
    ]

    # ── 1단계: OrderBlock SL/TP 파라미터 튜닝 ──────────────
    print("=" * 68)
    print("[1단계] OrderBlock SL/TP 파라미터 튜닝 (10코인 x 6조합)")
    print("=" * 68)

    SL_TP_COMBOS = [
        (1.0, 2.0, "SL×1.0 TP×2.0"),
        (1.2, 2.5, "SL×1.2 TP×2.5"),
        (1.5, 3.0, "SL×1.5 TP×3.0"),  # 현재값
        (1.5, 4.0, "SL×1.5 TP×4.0"),
        (2.0, 3.0, "SL×2.0 TP×3.0"),
        (2.0, 4.0, "SL×2.0 TP×4.0"),
    ]

    combo_results = {label: {"wins":0,"total":0,"ev_list":[]} for _,_,label in SL_TP_COMBOS}

    for market in MARKETS:
        df = await fetch_ohlcv(market, "1d", 365)
        if df is None or len(df) < 100:
            continue
        for sl_m, tp_m, label in SL_TP_COMBOS:
            bt  = TuningBacktester(sl_atr_mult=sl_m, tp_atr_mult=tp_m)
            res = bt.run(df, OrderBlockStrategy, market)
            if res["total"] > 0:
                combo_results[label]["total"] += res["total"]
                combo_results[label]["wins"]  += int(res["wr"] * res["total"] / 100)
                combo_results[label]["ev_list"].append(res["ev"])

    print(f"\n  {'파라미터조합':<22} {'통합승률':>8} {'평균기댓값':>10} {'양수코인':>8} {'총거래':>6}")
    print("-" * 60)
    best_label  = None
    best_ev     = -999
    best_sl_tp  = (1.5, 3.0)

    for (sl_m, tp_m, label), r in zip(SL_TP_COMBOS, combo_results.values()):
        if r["total"] == 0:
            continue
        wr     = r["wins"] / r["total"] * 100
        avg_ev = np.mean(r["ev_list"]) if r["ev_list"] else 0
        pos    = sum(1 for e in r["ev_list"] if e > 0)
        icon   = "✅" if avg_ev > 0.5 and wr >= 45 else "🟡" if avg_ev > 0 else "❌"
        print(f"  {icon} {label:<22} {wr:>7.1f}% {avg_ev:>+10.4f} "
              f"{pos:>4}/{len(r['ev_list'])} {r['total']:>6}회")
        if avg_ev > best_ev:
            best_ev    = avg_ev
            best_label = label
            best_sl_tp = (sl_m, tp_m)

    print(f"\n  → 최적 파라미터: {best_label}  (기댓값 {best_ev:+.4f}%)")

    # ── 2단계: 코인별 최적 전략 매핑 ──────────────────────
    print("\n" + "=" * 68)
    print("[2단계] 코인별 최적 전략 확정")
    print("=" * 68)

    CANDIDATE_STRATEGIES = {
        "OrderBlock":       OrderBlockStrategy,
        "MACD_Cross":       MACDCrossStrategy,
        "BollingerSqueeze": BollingerSqueezeStrategy,
    }

    best_sl, best_tp = best_sl_tp
    coin_strategy_map = {}
    all_valid = []

    for market in MARKETS:
        df = await fetch_ohlcv(market, "1d", 365)
        if df is None or len(df) < 100:
            continue
        best_strat_for_coin = None
        best_ev_for_coin    = -999
        coin_results        = {}

        for strat_name, strat_cls in CANDIDATE_STRATEGIES.items():
            sl_m = best_sl if strat_name == "OrderBlock" else 1.5
            tp_m = best_tp if strat_name == "OrderBlock" else 3.0
            bt   = TuningBacktester(sl_atr_mult=sl_m, tp_atr_mult=tp_m)
            res  = bt.run(df, strat_cls, market)
            coin_results[strat_name] = res
            if res["total"] >= 3 and res["ev"] > best_ev_for_coin:
                best_ev_for_coin    = res["ev"]
                best_strat_for_coin = strat_name

        # 결과 출력
        print(f"\n  {market}:")
        for sn, r in coin_results.items():
            if r["total"] == 0:
                continue
            icon = "★" if sn == best_strat_for_coin else " "
            verdict = "✅" if r["ev"] > 0 else "❌"
            print(f"    {icon}{verdict} {sn:<20} 승률={r['wr']:.1f}% "
                  f"기댓값={r['ev']:+.4f} 거래={r['total']}회")

        if best_strat_for_coin and best_ev_for_coin > 0:
            coin_strategy_map[market] = {
                "strategy": best_strat_for_coin,
                "ev":       best_ev_for_coin,
                "sl_mult":  best_sl if best_strat_for_coin=="OrderBlock" else 1.5,
                "tp_mult":  best_tp if best_strat_for_coin=="OrderBlock" else 3.0,
            }
            all_valid.append(market)

    # ── 3단계: 최종 코인×전략 매핑 테이블 출력 ────────────
    print("\n" + "=" * 68)
    print("[3단계] 최종 코인×전략 매핑 테이블")
    print("=" * 68)
    print(f"\n  {'코인':<12} {'전략':<20} {'기댓값':>8} {'SL배수':>6} {'TP배수':>6}")
    print("-" * 56)
    for market, cfg in coin_strategy_map.items():
        print(f"  ✅ {market:<10} {cfg['strategy']:<20} "
              f"{cfg['ev']:>+8.4f} {cfg['sl_mult']:>6.1f}x {cfg['tp_mult']:>6.1f}x")

    excluded = [m for m in MARKETS if m not in coin_strategy_map]
    if excluded:
        print(f"\n  ❌ 제외 코인 (모든 전략 기댓값 ≤ 0): {excluded}")

    print(f"""
  ━━━ 다음 단계 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  유효 코인 수: {len(coin_strategy_map)}/10개
  위 매핑 테이블을 settings.py에 적용하면
  코인별로 다른 전략을 자동 선택합니다.

  3일차: 위 매핑을 settings.py + signal_combiner.py에 적용
  4일차: 페이퍼 트레이딩 24시간 검증
  5일차: ML 재학습 결과 확인 후 통합 여부 결정
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")

asyncio.run(main())
