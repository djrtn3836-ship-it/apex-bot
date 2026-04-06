# risk/stop_loss/atr_stop.py — ATR 기반 동적 손절/익절
"""
ATR(Average True Range) 기반 동적 SL/TP 계산
- SL = 진입가 - (ATR × sl_multiplier)   기본 1.5×
- TP = 진입가 + (ATR × tp_multiplier)   기본 3.0×
- 손익비 2:1 이상 유지
- 14봉 ATR (Wilder 방식)
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from utils.logger import logger


@dataclass
class StopLevels:
    stop_loss:   float   # 손절가
    take_profit: float   # 익절가
    atr:         float   # 현재 ATR 값
    sl_pct:      float   # 손절 % (음수)
    tp_pct:      float   # 익절 % (양수)
    rr_ratio:    float   # 손익비



# ── 코인별 ATR 프로필 ──────────────────────────────────────────────
COIN_ATR_PROFILES = {
    "KRW-BTC":  {"min_sl": 0.010, "max_sl": 0.025, "sl_mult": 1.5, "tp_mult": 3.0},
    "KRW-ETH":  {"min_sl": 0.012, "max_sl": 0.028, "sl_mult": 1.5, "tp_mult": 3.0},
    "KRW-XRP":  {"min_sl": 0.015, "max_sl": 0.035, "sl_mult": 2.0, "tp_mult": 4.0},
    "KRW-SOL":  {"min_sl": 0.015, "max_sl": 0.035, "sl_mult": 2.0, "tp_mult": 4.0},
    "KRW-DOGE": {"min_sl": 0.018, "max_sl": 0.040, "sl_mult": 2.0, "tp_mult": 4.0},
    "KRW-AVAX": {"min_sl": 0.018, "max_sl": 0.038, "sl_mult": 2.0, "tp_mult": 4.0},
    "KRW-LINK": {"min_sl": 0.018, "max_sl": 0.038, "sl_mult": 2.0, "tp_mult": 4.0},
    "KRW-ATOM": {"min_sl": 0.018, "max_sl": 0.038, "sl_mult": 2.0, "tp_mult": 4.0},
    "KRW-DOT":  {"min_sl": 0.018, "max_sl": 0.038, "sl_mult": 2.0, "tp_mult": 4.0},
    "KRW-ADA":  {"min_sl": 0.018, "max_sl": 0.038, "sl_mult": 2.0, "tp_mult": 4.0},
    "KRW-SHIB": {"min_sl": 0.020, "max_sl": 0.045, "sl_mult": 2.0, "tp_mult": 4.0},
    # 동적 스캐너 포착 코인 기본값
    "DEFAULT":  {"min_sl": 0.015, "max_sl": 0.035, "sl_mult": 2.0, "tp_mult": 4.0},
    # 초저가 코인 (1원 미만)
    "PENNY":    {"min_sl": 0.015, "max_sl": 0.030, "sl_mult": 1.5, "tp_mult": 4.5},
}

class ATRStopLoss:
    """
    ATR 기반 동적 손절/익절 계산기
    사용법:
        atr_sl = ATRStopLoss()
        levels = atr_sl.calculate(df, entry_price=121600)
        print(levels.stop_loss, levels.take_profit)
    """
    ATR_PERIOD    = 14
    SL_MULTIPLIER = 1.5    # ATR × 1.5 = 손절폭
    TP_MULTIPLIER = 3.0    # ATR × 3.0 = 익절폭 (손익비 2:1)
    MIN_SL_PCT    = 0.005  # 최소 손절 0.5%
    MAX_SL_PCT    = 0.08   # 최대 손절 8%

    def __init__(
        self,
        sl_multiplier: float = 1.5,
        tp_multiplier: float = 3.0,
    ):
        self.sl_mult = sl_multiplier
        self.tp_mult = tp_multiplier

    def calculate(self, df: pd.DataFrame, entry_price: float,
                  market: str = "") -> StopLevels:
        """
        df: OHLCV DataFrame (columns: open, high, low, close, volume)
        entry_price: 매수 진입가
        market: 코인 마켓명 (코인별 프로필 적용)
        Returns: StopLevels 데이터클래스
        """
        atr = self._calc_atr(df)

        # 코인별 프로필 적용
        if entry_price < 1.0:
            profile = COIN_ATR_PROFILES.get("PENNY", COIN_ATR_PROFILES["DEFAULT"])
        else:
            profile = COIN_ATR_PROFILES.get(market, COIN_ATR_PROFILES["DEFAULT"])

        sl_mult = profile["sl_mult"]
        tp_mult = profile["tp_mult"]
        min_sl  = profile["min_sl"]
        max_sl  = profile["max_sl"]

        raw_sl_dist = atr * sl_mult
        raw_tp_dist = atr * tp_mult

        # 코인 프로필 기반 캡 적용
        sl_dist = max(
            entry_price * min_sl,
            min(raw_sl_dist, entry_price * max_sl),
        )
        tp_dist = sl_dist * (tp_mult / sl_mult)

        stop_loss   = entry_price - sl_dist
        take_profit = entry_price + tp_dist

        sl_pct = -sl_dist / entry_price
        tp_pct =  tp_dist / entry_price
        rr     = tp_dist  / sl_dist if sl_dist > 0 else 2.0

        logger.debug(
            f"[ATR-SL] entry={entry_price:.2f} ATR={atr:.4f} "
            f"SL={stop_loss:.2f}({sl_pct*100:.2f}%) "
            f"TP={take_profit:.2f}({tp_pct*100:.2f}%) "
            f"RR={rr:.2f}"
        )
        return StopLevels(
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr=atr,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
            rr_ratio=rr,
        )

    def _calc_atr(self, df: pd.DataFrame) -> float:
        """Wilder ATR 계산 (14봉)"""
        if len(df) < self.ATR_PERIOD + 1:
            # 데이터 부족: 최근 봉의 HL 레인지 평균 사용
            recent = df.tail(min(5, len(df)))
            hl_range = (recent["high"] - recent["low"]).mean()
            return float(hl_range) if not pd.isna(hl_range) else 0.0

        high  = df["high"].values.astype(float)
        low   = df["low"].values.astype(float)
        close = df["close"].values.astype(float)

        # True Range 계산
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:]  - close[:-1]),
            ),
        )

        # Wilder 평활화 (EMA 방식)
        n   = self.ATR_PERIOD
        atr = np.zeros(len(tr))
        if len(tr) >= n:
            atr[n - 1] = tr[:n].mean()
            for i in range(n, len(tr)):
                atr[i] = (atr[i - 1] * (n - 1) + tr[i]) / n

        last_atr = atr[-1]
        return float(last_atr) if last_atr > 0 else float(tr[-1]) if len(tr) > 0 else 0.0

    def get_dynamic_levels(
        self,
        df: pd.DataFrame,
        entry_price: float,
        current_price: float,
        profit_pct: float,
    ) -> StopLevels:
        """
        수익 구간별 SL/TP 동적 조정
        - 수익 +3% 이상: SL을 진입가(BEP)로 이동 (리스크 프리)
        - 수익 +5% 이상: SL을 +2%로 이동 (수익 보전)
        """
        levels = self.calculate(df, entry_price)

        if profit_pct >= 0.05:
            # 수익 +5% 이상: +2% 보전
            new_sl = entry_price * 1.02
            levels = StopLevels(
                stop_loss   = max(levels.stop_loss, new_sl),
                take_profit = levels.take_profit,
                atr         = levels.atr,
                sl_pct      = (max(levels.stop_loss, new_sl) - entry_price) / entry_price,
                tp_pct      = levels.tp_pct,
                rr_ratio    = levels.rr_ratio,
            )
            logger.debug(f"[ATR-SL] 수익보전 SL 이동: {new_sl:.2f} (+2%)")

        elif profit_pct >= 0.03:
            # 수익 +3% 이상: 손익분기점으로 이동
            new_sl = entry_price * 1.001  # 수수료 감안 +0.1%
            levels = StopLevels(
                stop_loss   = max(levels.stop_loss, new_sl),
                take_profit = levels.take_profit,
                atr         = levels.atr,
                sl_pct      = (max(levels.stop_loss, new_sl) - entry_price) / entry_price,
                tp_pct      = levels.tp_pct,
                rr_ratio    = levels.rr_ratio,
            )
            logger.debug(f"[ATR-SL] BEP SL 이동: {new_sl:.2f}")

        return levels
