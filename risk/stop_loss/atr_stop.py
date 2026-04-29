"""ATR   / 
-       (  )
- : BTC/ETH///  
- ATR%     SL/TP"""
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
    """(  )
      SL ,  SL"""
    if entry_price >= 10_000_000:       # BTC급 (1000만원 이상)
        return {"min_sl": 0.010, "max_sl": 0.015, "sl_mult": 1.5, "tp_mult": 3.0}
    elif entry_price >= 1_000_000:      # ETH/BNB급 (100만원 이상)
        return {"min_sl": 0.012, "max_sl": 0.018, "sl_mult": 1.8, "tp_mult": 3.5}
    elif entry_price >= 100_000:        # SOL/AVAX급 (10만원 이상)
        return {"min_sl": 0.015, "max_sl": 0.020, "sl_mult": 2.0, "tp_mult": 4.0}
    elif entry_price >= 1_000:          # 중가 코인 (1000원 이상)
        return {"min_sl": 0.018, "max_sl": 0.020, "sl_mult": 2.0, "tp_mult": 4.0}
    elif entry_price >= 10:             # 저가 코인 (10원 이상)
        return {"min_sl": 0.020, "max_sl": 0.022, "sl_mult": 2.5, "tp_mult": 5.0}
    elif entry_price >= 1:              # 초저가 (1원 이상)
        return {"min_sl": 0.025, "max_sl": 0.025, "sl_mult": 2.5, "tp_mult": 5.0}
    else:                               # 극초저가 (1원 미만, SHIB류)
        return {"min_sl": 0.025, "max_sl": 0.025, "sl_mult": 3.0, "tp_mult": 6.0}




def _get_surge_profile(entry_price: float) -> dict:
    """SURGE_FASTENTRY 전용 SL/TP 프로파일 [FIX-SURGE-PROFILE]
    일반 프로파일 대비 SL 30% 타이트, RR 2.0 유지"""
    if entry_price >= 10_000_000:    # BTC급
        return {"min_sl": 0.007, "max_sl": 0.010, "sl_mult": 1.5, "tp_mult": 3.0}
    elif entry_price >= 1_000_000:   # ETH/BNB급
        return {"min_sl": 0.008, "max_sl": 0.012, "sl_mult": 1.8, "tp_mult": 3.5}
    elif entry_price >= 100_000:     # SOL/AVAX급
        return {"min_sl": 0.010, "max_sl": 0.013, "sl_mult": 2.0, "tp_mult": 4.0}
    elif entry_price >= 1_000:       # 중가 코인
        return {"min_sl": 0.012, "max_sl": 0.015, "sl_mult": 2.0, "tp_mult": 4.0}
    elif entry_price >= 10:          # 저가 코인 ← SURGE 주력 구간
        return {"min_sl": 0.013, "max_sl": 0.015, "sl_mult": 2.0, "tp_mult": 4.0}
    elif entry_price >= 1:           # 초저가
        return {"min_sl": 0.015, "max_sl": 0.018, "sl_mult": 2.0, "tp_mult": 4.0}
    else:                            # 극초저가 (SHIB류)
        return {"min_sl": 0.018, "max_sl": 0.020, "sl_mult": 2.5, "tp_mult": 5.0}

class ATRStopLoss:
    """ATR   / 
    -    
    - ATR%   
    -   SL"""
    ATR_PERIOD = 14

    def __init__(self, sl_multiplier: float = 2.0, tp_multiplier: float = 4.0):
        self.sl_mult = sl_multiplier
        self.tp_mult = tp_multiplier

    def calculate(self, df: pd.DataFrame, entry_price: float,
                  market: str = "", global_regime=None,
                  is_surge: bool = False, local_regime=None) -> "StopLevels":  # [FIX-SURGE-SIG]
        atr = self._calc_atr(df)
        # [FIX-SURGE-PROFILE] SURGE 전용 프로파일 분기
        profile = _get_surge_profile(entry_price) if is_surge else _get_profile_by_price(entry_price)

        sl_mult = profile["sl_mult"]
        tp_mult = profile["tp_mult"]
        min_sl  = profile["min_sl"]
        max_sl  = profile["max_sl"]

        # [FIX-ATR-BAND] ATR% 보정 구간 세분화 (기존 사각지대 1%~5% 해소)
        atr_pct = atr / entry_price if entry_price > 0 else 0.02
        if atr_pct > 0.05:            # 극고변동성 (5%+): SL 넓게
            sl_mult *= 1.20
            tp_mult *= 1.20
        elif atr_pct > 0.03:          # 고변동성 (3~5%): 유지
            pass
        elif atr_pct > 0.015:         # 중변동성 (1.5~3%) ← SURGE 주력
            sl_mult *= 0.85
            tp_mult *= 0.90
        elif atr_pct > 0.008:         # 저중변동성 (0.8~1.5%)
            sl_mult *= 0.75
            tp_mult *= 0.80
        else:                          # 저변동성 (0.8% 미만)
            sl_mult *= 0.65
            tp_mult *= 0.70

        # [FIX-LOCAL-REGIME] 개별 코인 로컬 레짐 보정
        if local_regime is not None:
            _lr = str(getattr(local_regime, "value", local_regime)).upper()
            if "TRENDING_DOWN" in _lr:
                sl_mult *= 0.70   # 하락추세: 매우 타이트
                tp_mult *= 0.75
                logger.debug(f"[ATR-SL] TRENDING_DOWN → SL/TP 대폭 축소 ({market})")
            elif "VOLATILE" in _lr:
                sl_mult *= 0.80   # 급등락: 타이트
                tp_mult *= 0.85
                logger.debug(f"[ATR-SL] VOLATILE → SL/TP 축소 ({market})")
            elif "RANGING" in _lr:
                sl_mult *= 0.85   # 횡보: 약간 타이트
                logger.debug(f"[ATR-SL] RANGING → SL 축소 ({market})")
            elif "BEAR_REVERSAL" in _lr:
                sl_mult *= 0.75
                tp_mult *= 0.80
                logger.debug(f"[ATR-SL] BEAR_REVERSAL → SL/TP 축소 ({market})")
        # GlobalRegime 기반 동적 SL/TP 배수 조정 (Phase 8)
        if global_regime is not None:
            # [FIX-REGIME-CMP] Enum/문자열 모두 처리, 대소문자 무관
            _gr = str(getattr(global_regime, "value", global_regime)).upper()
            if _gr == "BEAR":
                sl_mult *= 0.70   # BEAR: SL 타이트 (손실 최소화)
                tp_mult *= 0.80
                logger.debug(f"[ATR-SL] BEAR 글로벌레짐 → SL 타이트 ({market})")
            elif _gr == "BEAR_WATCH":
                sl_mult *= 0.85   # BEAR_WATCH: SL 약간 타이트
                logger.debug(f"[ATR-SL] BEAR_WATCH 글로벌레짐 → SL 축소 ({market})")
            elif _gr == "RECOVERY":
                sl_mult *= 0.95   # RECOVERY: 약간 보수적
                logger.debug(f"[ATR-SL] RECOVERY 글로벌레짐 → SL 소폭 축소 ({market})")
            elif _gr == "BULL":
                sl_mult *= 1.10   # BULL: SL 여유있게 (추세 추종)
                tp_mult *= 1.20
                logger.debug(f"[ATR-SL] BULL 글로벌레짐 → SL/TP 확장 ({market})")


        raw_sl_dist = atr * sl_mult

        # [SL-OPT] 슬리피지 보정: 실제 체결가는 SL보다 불리
        # 알트코인 평균 슬리피지 0.08% 반영 → SL을 약간 타이트하게
        SLIP_BUFFER = 0.0008  # 0.08% 슬리피지 버퍼
        raw_sl_dist = raw_sl_dist * (1 - SLIP_BUFFER)
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
        """SL  
        +3%: (BEP) 
        +5%: +2%  
        +10%: +5%"""
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