import shutil, py_compile, sys
from pathlib import Path

TARGET = "risk/stop_loss/atr_stop.py"
BACKUP = "risk/stop_loss/atr_stop.py.bak_dynamic"
shutil.copy(TARGET, BACKUP)
print("✅ 백업 완료:", BACKUP)

NEW_CONTENT = '''"""
ATR 기반 동적 손절/익절 계산
- 코인 현재가 기준 자동 프로필 선택 (고정 딕셔너리 제거)
- 가격대별: BTC급/ETH급/중가/저가/초저가 자동 분류
- ATR% 실시간 반영으로 변동성 국면별 SL/TP 자동 조정
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass
from utils.logger import logger


@dataclass
class StopLevels:
    stop_loss:   float
    take_profit: float
    atr:         float
    sl_pct:      float
    tp_pct:      float
    rr_ratio:    float


def _get_profile_by_price(entry_price: float) -> dict:
    """
    현재가 기준 자동 프로필 반환 (고정 딕셔너리 불필요)
    가격대가 높을수록 SL 타이트, 낮을수록 SL 넓게
    """
    if entry_price >= 10_000_000:       # BTC급 (1000만원 이상)
        return {"min_sl": 0.010, "max_sl": 0.025, "sl_mult": 1.5, "tp_mult": 3.0}
    elif entry_price >= 1_000_000:      # ETH/BNB급 (100만원 이상)
        return {"min_sl": 0.012, "max_sl": 0.030, "sl_mult": 1.8, "tp_mult": 3.5}
    elif entry_price >= 100_000:        # SOL/AVAX급 (10만원 이상)
        return {"min_sl": 0.015, "max_sl": 0.035, "sl_mult": 2.0, "tp_mult": 4.0}
    elif entry_price >= 1_000:          # 중가 코인 (1000원 이상)
        return {"min_sl": 0.018, "max_sl": 0.040, "sl_mult": 2.0, "tp_mult": 4.0}
    elif entry_price >= 10:             # 저가 코인 (10원 이상)
        return {"min_sl": 0.020, "max_sl": 0.050, "sl_mult": 2.5, "tp_mult": 5.0}
    elif entry_price >= 1:              # 초저가 (1원 이상)
        return {"min_sl": 0.025, "max_sl": 0.060, "sl_mult": 2.5, "tp_mult": 5.0}
    else:                               # 극초저가 (1원 미만, SHIB류)
        return {"min_sl": 0.025, "max_sl": 0.070, "sl_mult": 3.0, "tp_mult": 6.0}


class ATRStopLoss:
    """
    ATR 기반 동적 손절/익절 계산기
    - 가격대 자동 프로필 선택
    - ATR% 실시간 변동성 반영
    - 수익 구간별 SL 동적 이동
    """
    ATR_PERIOD = 14

    def __init__(self, sl_multiplier: float = 2.0, tp_multiplier: float = 4.0):
        self.sl_mult = sl_multiplier
        self.tp_mult = tp_multiplier

    def calculate(self, df: pd.DataFrame, entry_price: float,
                  market: str = "") -> StopLevels:
        atr = self._calc_atr(df)
        profile = _get_profile_by_price(entry_price)

        sl_mult = profile["sl_mult"]
        tp_mult = profile["tp_mult"]
        min_sl  = profile["min_sl"]
        max_sl  = profile["max_sl"]

        # ATR% 기반 변동성 국면 추가 보정
        atr_pct = atr / entry_price if entry_price > 0 else 0.02
        if atr_pct > 0.05:           # 고변동성: SL 더 넓게
            sl_mult *= 1.2
            tp_mult *= 1.2
        elif atr_pct < 0.01:         # 저변동성: SL 타이트하게
            sl_mult *= 0.8
            tp_mult *= 0.8

        raw_sl_dist = atr * sl_mult
        sl_dist = max(
            entry_price * min_sl,
            min(raw_sl_dist, entry_price * max_sl),
        )
        tp_dist     = sl_dist * (tp_mult / sl_mult)
        stop_loss   = entry_price - sl_dist
        take_profit = entry_price + tp_dist
        sl_pct      = -sl_dist / entry_price
        tp_pct      =  tp_dist / entry_price
        rr          = tp_dist / sl_dist if sl_dist > 0 else 2.0

        logger.debug(
            f"[ATR-SL] {market} entry={entry_price:,.0f} "
            f"ATR={atr:.4f}({atr_pct*100:.2f}%) "
            f"SL={stop_loss:,.0f}({sl_pct*100:.2f}%) "
            f"TP={take_profit:,.0f}({tp_pct*100:.2f}%) RR={rr:.2f}"
        )
        return StopLevels(
            stop_loss=stop_loss, take_profit=take_profit,
            atr=atr, sl_pct=sl_pct, tp_pct=tp_pct, rr_ratio=rr,
        )

    def _calc_atr(self, df: pd.DataFrame) -> float:
        if len(df) < self.ATR_PERIOD + 1:
            recent   = df.tail(min(5, len(df)))
            hl_range = (recent["high"] - recent["low"]).mean()
            return float(hl_range) if not pd.isna(hl_range) else 0.0

        high  = df["high"].values.astype(float)
        low   = df["low"].values.astype(float)
        close = df["close"].values.astype(float)

        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:]  - close[:-1]),
            ),
        )
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
        market: str = "",
    ) -> StopLevels:
        """
        수익 구간별 SL 동적 이동
        +3%: 손익분기점(BEP)으로 이동
        +5%: +2% 수익 보전
        +10%: +5% 수익 보전
        """
        levels = self.calculate(df, entry_price, market)

        if profit_pct >= 0.10:
            new_sl = entry_price * 1.05
        elif profit_pct >= 0.05:
            new_sl = entry_price * 1.02
        elif profit_pct >= 0.03:
            new_sl = entry_price * 1.001
        else:
            return levels

        new_sl = max(levels.stop_loss, new_sl)
        return StopLevels(
            stop_loss   = new_sl,
            take_profit = levels.take_profit,
            atr         = levels.atr,
            sl_pct      = (new_sl - entry_price) / entry_price,
            tp_pct      = levels.tp_pct,
            rr_ratio    = levels.rr_ratio,
        )
'''

Path(TARGET).write_text(NEW_CONTENT, encoding="utf-8")

try:
    py_compile.compile(TARGET, doraise=True)
    print("✅ 문법 검사 OK")
except py_compile.PyCompileError as e:
    print("❌ 문법 오류:", e)
    shutil.copy(BACKUP, TARGET)
    print("🔁 원본 복구 완료")
    sys.exit(1)

print("🎉 ATR 가격 기반 동적 프로필 적용 완료!")
