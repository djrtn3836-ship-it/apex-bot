# signals/filters/trend_filter.py — EMA200 + 다중 TF 트렌드 필터
"""
Layer 2 트렌드 필터:
  1) 일봉 EMA(200) 위: 강세장 → 매수 허용
  2) 일봉 EMA(200) 아래: 약세장 → 매수 차단 (BEAR_REVERSAL만 허용)
  3) 4시간봉 EMA(50) 방향 → 중기 트렌드 확인
  4) 시장 레짐 판단: TRENDING / RANGING / VOLATILE
"""

import numpy as np
import pandas as pd
from typing import Optional
from utils.logger import logger


class TrendFilter:
    EMA200_PERIOD = 200
    EMA50_PERIOD  = 50
    EMA20_PERIOD  = 20
    ADX_PERIOD    = 14
    ADX_TRENDING  = 25   # ADX > 25 → 추세장
    ADX_VOLATILE  = 40   # ADX > 40 → 강한 추세/변동성

    # ── 주요 공개 메서드 ──────────────────────────────────────
    def is_buy_allowed(
        self,
        daily_df: pd.DataFrame,
        h4_df: Optional[pd.DataFrame] = None,
        strategy: str = "default",
    ) -> dict:
        """
        매수 허용 여부 + 트렌드 정보 반환
        Returns: {
            "allowed": bool,
            "regime":  "BULL" | "BEAR" | "NEUTRAL",
            "ema200":  float,
            "reason":  str,
        }
        """
        result = self._check_daily(daily_df)

        # BEAR_REVERSAL 전략은 약세장에서도 허용
        if not result["allowed"] and strategy == "BEAR_REVERSAL":
            result["allowed"] = True
            result["reason"]  += " (BEAR_REVERSAL 예외)"

        # 4시간봉 추가 확인
        if result["allowed"] and h4_df is not None:
            h4_check = self._check_h4(h4_df)
            if not h4_check["trending"]:
                result["reason"] += f" | 4H:{h4_check['reason']}"
        """Returns: TRENDING_UP | TRENDING_DOWN | RANGING | VOLATILE"""
        return result

    def get_regime(self, df: pd.DataFrame) -> str:
        """
        시장 레짐 감지
        Returns: "TRENDING_UP" | "TRENDING_DOWN" | "RANGING" | "VOLATILE"
        """
        if len(df) < self.ADX_PERIOD + 5:
            return "RANGING"

        adx   = self._calc_adx(df)
        close = df["close"].iloc[-1]
        ema20 = self._ema(df["close"], self.EMA20_PERIOD).iloc[-1]

        if adx > self.ADX_VOLATILE:
            return "VOLATILE"
        elif adx > self.ADX_TRENDING:
            return "TRENDING_UP" if close > ema20 else "TRENDING_DOWN"
        else:
            return "RANGING"

    # ── 내부 메서드 ───────────────────────────────────────────
    def _check_daily(self, df: pd.DataFrame) -> dict:
        if len(df) < self.EMA200_PERIOD:
            return {
                "allowed": True,
                "regime":  "NEUTRAL",
                "ema200":  0.0,
                "reason":  "일봉 데이터 부족 → 매수 허용",
            }

        close  = df["close"].iloc[-1]
        ema200 = self._ema(df["close"], self.EMA200_PERIOD).iloc[-1]
        diff_pct = (close - ema200) / ema200 * 100

        if close > ema200:
            return {
                "allowed": True,
                "regime":  "BULL",
                "ema200":  ema200,
                "reason":  f"일봉 EMA200 위 (+{diff_pct:.1f}%) → 매수 허용",
            }
        elif diff_pct > -60.0:
            return {
                "allowed": True,
                "regime":  "BEAR",
                "ema200":  ema200,
                "reason":  f"일봉 EMA200 하회({diff_pct:.1f}%) → BEAR 완화 허용",
            }
        else:
            return {
                "allowed": False,
                "regime":  "BEAR",
                "ema200":  ema200,
                "reason":  f"일봉 EMA200 하회({diff_pct:.1f}%) → 매수 차단 (과도 하락)",
            }

    def _check_h4(self, df: pd.DataFrame) -> dict:
        if len(df) < self.EMA50_PERIOD:
            return {"trending": True, "reason": "4H 데이터 부족"}
        close = df["close"].iloc[-1]
        ema50 = self._ema(df["close"], self.EMA50_PERIOD).iloc[-1]
        trending = close > ema50 * 0.98   # 2% 여유
        return {
            "trending": trending,
            "reason":   f"4H EMA50={'위' if trending else '아래'}",
        }

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def _calc_adx(self, df: pd.DataFrame) -> float:
        high  = df["high"].values
        low   = df["low"].values
        close = df["close"].values
        n     = self.ADX_PERIOD

        if len(df) < n * 2:
            return 20.0

        tr_list, pdm_list, ndm_list = [], [], []
        for i in range(1, len(df)):
            tr  = max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1]))
            pdm = max(high[i]-high[i-1], 0)
            ndm = max(low[i-1]-low[i],   0)
            if pdm < ndm: pdm = 0
            elif ndm < pdm: ndm = 0
            tr_list.append(tr); pdm_list.append(pdm); ndm_list.append(ndm)

        atr  = self._wilder_smooth(tr_list,  n)
        pdi  = 100 * self._wilder_smooth(pdm_list, n) / (atr + 1e-9)
        ndi  = 100 * self._wilder_smooth(ndm_list, n) / (atr + 1e-9)
        dx   = 100 * abs(pdi - ndi) / (pdi + ndi + 1e-9)
        return float(dx)

    @staticmethod
    def _wilder_smooth(data: list, n: int) -> float:
        if len(data) < n:
            return float(np.mean(data)) if data else 0.0
        val = sum(data[:n])
        for d in data[n:]:
            val = val - val / n + d
        return val
