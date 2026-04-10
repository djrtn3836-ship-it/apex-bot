"""APEX BOT -   +    
 :
  BULL     : EMA200  + ADX > 25  -> trend_following + ml_strategy
  BEAR     : EMA200  + ADX > 25 -> mean_reversion + rsi_divergence
  RANGE    : ADX < 20              -> mean_reversion + volume_spike
  VOLATILE : ADX > 40              -> rsi_divergence + volume_spike"""
import pandas as pd
from typing import Dict, Tuple
from loguru import logger

from backtesting.backtester import Backtester, BacktestResult
from backtesting.signal_generator import (
    signal_trend_following,
    signal_mean_reversion,
    signal_ml_strategy,
    signal_rsi_divergence,
    signal_volume_spike,
    signal_macd_momentum,
    _ema, _adx,
)


REGIME_STRATEGIES = {
    "BULL":     [("trend_following", signal_trend_following, 1.5),
                 ("ml_strategy",     signal_ml_strategy,     1.0)],
    "BEAR":     [("mean_reversion",  signal_mean_reversion,  1.2),
                 ("rsi_divergence",  signal_rsi_divergence,  0.8)],
    "RANGE":    [("mean_reversion",  signal_mean_reversion,  1.0),
                 ("volume_spike",    signal_volume_spike,    1.0)],
    "VOLATILE": [("rsi_divergence",  signal_rsi_divergence,  1.0),
                 ("volume_spike",    signal_volume_spike,    1.2)],
}


def detect_regime(
    df: pd.DataFrame,
    adx_period: int = 14,
    ema_period: int = 200,
    adx_trend: float = 20.0,
    adx_volatile: float = 35.0,
) -> pd.Series:
    """-> BULL/BEAR/RANGE/VOLATILE
     :
    - BULL: EMA20 > EMA50 ( ) OR EMA50 > EMA200 ( )
    - BEAR: EMA20 < EMA50 AND EMA50 < EMA200 (  )
    - ADX  : 25 -> 20"""
    ema20  = _ema(df["close"], 20)
    ema50  = _ema(df["close"], 50)
    ema200 = _ema(df["close"], ema_period)
    adx    = _adx(df, adx_period)

    regime = pd.Series("RANGE", index=df.index)

    # 추세 강도
    trend_mask    = adx > adx_trend
    volatile_mask = adx > adx_volatile

    # 방향성: 단기 or 중기 상승
    bull_dir = (ema20 > ema50) | (ema50 > ema200)
    bear_dir = (ema20 < ema50) & (ema50 < ema200)

    regime[volatile_mask]                            = "VOLATILE"
    regime[trend_mask & bull_dir & ~volatile_mask]   = "BULL"
    regime[trend_mask & bear_dir & ~volatile_mask]   = "BEAR"
    # 초반 불안정 구간
    regime.iloc[:50] = "RANGE"
    return regime


def build_regime_signal(
    df: pd.DataFrame,
    min_score: float = 1.0,
) -> Tuple[pd.Series, pd.DataFrame]:
    """docstring"""
    regime = detect_regime(df)
    counts = regime.value_counts().to_dict()
    logger.info(" : " + str({k: int(v) for k, v in counts.items()}))

    all_signals = {
        "trend_following": signal_trend_following(df),
        "mean_reversion":  signal_mean_reversion(df),
        "ml_strategy":     signal_ml_strategy(df),
        "rsi_divergence":  signal_rsi_divergence(df),
        "volume_spike":    signal_volume_spike(df),
        "macd_momentum":   signal_macd_momentum(df),
    }

    buy_score  = pd.Series(0.0, index=df.index)
    sell_score = pd.Series(0.0, index=df.index)

    for reg, strat_list in REGIME_STRATEGIES.items():
        mask = (regime == reg)
        if not mask.any():
            continue
        for strat_name, _, weight in strat_list:
            sig = all_signals[strat_name]
            buy_score[mask]  += (sig[mask] == 1).astype(float)  * weight
            sell_score[mask] += (sig[mask] == -1).astype(float) * weight

    signal = pd.Series(0, index=df.index)
    signal[buy_score  >= min_score] = 1
    signal[sell_score >= min_score] = -1

    buy_cnt  = int((signal == 1).sum())
    sell_cnt = int((signal == -1).sum())
    hold_cnt = int((signal == 0).sum())
    logger.info(" : BUY=" + str(buy_cnt) + " SELL=" + str(sell_cnt) + " HOLD=" + str(hold_cnt))

    debug_df = pd.DataFrame({
        "regime":     regime,
        "buy_score":  buy_score,
        "sell_score": sell_score,
        "signal":     signal,
    })
    return signal, debug_df


class RegimeStrategyBacktester:
    """+"""

    def __init__(self, base_backtester: Backtester = None):
        self.bt = base_backtester or Backtester()

    def run(
        self,
        df:        pd.DataFrame,
        market:    str   = "KRW-BTC",
        min_score: float = 1.0,
    ) -> Tuple[BacktestResult, pd.DataFrame]:
        """docstring"""
        signal, debug_df = build_regime_signal(df, min_score)

        regime_counts = debug_df["regime"].value_counts()
        print("")
        print("  [" + market + "]  :")
        for reg, cnt in regime_counts.items():
            pct = cnt / len(debug_df) * 100
            bar = chr(9608) * int(pct / 5)
            print("    " + str(reg).ljust(10) + str(cnt).rjust(4) + "봉 (" + f"{pct:>5.1f}%) " + bar)

        result = self.bt._simulate(df, signal, "regime_adaptive", market)
        return result, debug_df

    def compare_min_score(
        self,
        df:     pd.DataFrame,
        market: str = "KRW-BTC",
        scores: list = None,
    ) -> Dict[float, BacktestResult]:
        """min_score"""
        if scores is None:
            scores = [0.5, 0.8, 1.0, 1.2, 1.5, 2.0]

        results = {}
        print("=" * 60)
        print("     min_score  (" + market + ")")
        print("=" * 60)
        print("  score                       ")
        print("-" * 60)

        for s in scores:
            r, _ = self.run(df, market, min_score=s)
            results[s] = r
            print(
                "  " + f"{s:>5.1f}  " +
                f"{r.total_return:>7.1f}%  " +
                f"{r.sharpe_ratio:>7.3f}  " +
                f"{r.win_rate:>6.1f}%  " +
                f"{r.max_drawdown:>7.1f}%  " +
                f"{r.total_trades:>6}"
            )

        print("=" * 60)
        best_s = max(results, key=lambda k: results[k].sharpe_ratio)
        print("   score: " + str(best_s) + " (샤프 " + f"{results[best_s].sharpe_ratio:.3f})")
        return results
