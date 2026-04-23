from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, List, Tuple
from loguru import logger
from strategies.base_strategy import BaseStrategy, Signal, SignalType
from strategies.v2.context.market_context import MarketContextEngine, MarketContext


@dataclass
class AnchoredVWAP:
    anchor_price: float
    anchor_idx: int
    anchor_type: str      # "high" / "low" / "volume_spike" / "gap"
    vwap: float
    upper1: float         # +1 표준편차
    upper2: float         # +2 표준편차
    lower1: float         # -1 표준편차
    lower2: float         # -2 표준편차
    strength: float       # 앵커 신뢰도 0~1


class VWAPReversionStrategy2(BaseStrategy):
    """
    VWAP 2.0 — 앵커드 VWAP 다중 밴드
    특정 이벤트(고점/저점/거래량 급증/갭) 기준 VWAP 계산
    단순 일일 VWAP 대비 훨씬 정확한 지지/저항 레벨 제공
    RANGING + TRENDING 레짐 모두 대응
    """
    NAME        = "VWAP_Reversion"
    DESCRIPTION = "앵커드 VWAP 2.0 — 이벤트 기반 다중 밴드 지지/저항"
    VERSION     = "2.0"

    # 파라미터
    ANCHOR_LOOKBACK    = 30     # 앵커 포인트 탐색 기간
    VOL_SPIKE_MULT     = 2.5    # 거래량 급증 기준 배수
    GAP_MIN_PCT        = 0.015  # 갭 최소 비율 1.5%
    ENTRY_BAND         = 2      # 진입 밴드 레벨 (lower2 터치 시 진입)
    MIN_RSI_OVERSOLD   = 38     # 과매도 RSI 기준
    MAX_RSI_OVERSOLD   = 48     # RSI 상한 (너무 높으면 진입 안함)
    TARGET_BAND        = 0      # 목표 밴드 (VWAP 중심선)
    MIN_CONFIDENCE     = 0.45

    def __init__(self):
        super().__init__()
        self._context_engine = MarketContextEngine()

    def generate_signal(self, df: pd.DataFrame, market: str = "") -> Optional[Signal]:
        try:
            if not self._enabled:
                return None
            if len(df) < self.ANCHOR_LOOKBACK + 10:
                return None

            ctx = self._context_engine.analyze(df, market)

            # 강한 하락 추세에서는 진입 금지
            if ctx.regime == "TRENDING_DOWN" and ctx.volatility_rank > 0.6:
                return None

            anchors = self._find_anchor_points(df)
            if not anchors:
                return None

            avwaps = [self._calc_anchored_vwap(df, a) for a in anchors]
            avwaps = [v for v in avwaps if v is not None]
            if not avwaps:
                return None

            signal = self._check_entry(df, avwaps, ctx, market)
            return signal

        except Exception as e:
            logger.warning(f"[VWAP2.0] {market} 오류: {e}")
            return None

    def _find_anchor_points(self, df: pd.DataFrame) -> List[Tuple[int, str]]:
        anchors: List[Tuple[int, str]] = []
        recent = df.iloc[-self.ANCHOR_LOOKBACK:]
        close  = recent["close"].values
        volume = recent["volume"].values
        high   = recent["high"].values
        low    = recent["low"].values
        avg_vol = np.mean(volume)
        base_idx = len(df) - self.ANCHOR_LOOKBACK

        # 1. 최근 최고점/최저점
        max_idx = int(np.argmax(high))
        min_idx = int(np.argmin(low))
        anchors.append((base_idx + max_idx, "high"))
        anchors.append((base_idx + min_idx, "low"))

        # 2. 거래량 급증일
        for i, v in enumerate(volume):
            if v > avg_vol * self.VOL_SPIKE_MULT:
                anchors.append((base_idx + i, "volume_spike"))

        # 3. 갭 발생일
        for i in range(1, len(close)):
            gap_pct = abs(close[i] - close[i-1]) / close[i-1] if close[i-1] > 0 else 0
            if gap_pct >= self.GAP_MIN_PCT:
                anchors.append((base_idx + i, "gap"))

        # 중복 제거 및 최근 5개만 유지
        seen = set()
        unique = []
        for idx, t in anchors:
            if idx not in seen:
                seen.add(idx)
                unique.append((idx, t))

        return sorted(unique, key=lambda x: x[0], reverse=True)[:5]

    def _calc_anchored_vwap(
        self, df: pd.DataFrame, anchor: Tuple[int, str]
    ) -> Optional[AnchoredVWAP]:
        try:
            anchor_idx, anchor_type = anchor
            if anchor_idx >= len(df):
                return None

            segment = df.iloc[anchor_idx:]
            if len(segment) < 3:
                return None

            typical = (segment["high"] + segment["low"] + segment["close"]) / 3
            vol     = segment["volume"]
            cum_tpv = (typical * vol).cumsum()
            cum_vol = vol.cumsum()
            vwap_series = cum_tpv / cum_vol.replace(0, np.nan)

            # 표준편차 밴드
            dev = typical - vwap_series
            std = float((dev ** 2 * vol).cumsum().iloc[-1] / cum_vol.iloc[-1]) ** 0.5

            vwap_now = float(vwap_series.iloc[-1])
            anchor_price = float(df["close"].iloc[anchor_idx])

            # 앵커 신뢰도
            strength_map = {
                "high": 0.9,
                "low": 0.9,
                "volume_spike": 0.8,
                "gap": 0.7,
            }
            strength = strength_map.get(anchor_type, 0.6)

            return AnchoredVWAP(
                anchor_price=anchor_price,
                anchor_idx=anchor_idx,
                anchor_type=anchor_type,
                vwap=vwap_now,
                upper1=vwap_now + std,
                upper2=vwap_now + 2 * std,
                lower1=vwap_now - std,
                lower2=vwap_now - 2 * std,
                strength=strength,
            )
        except Exception as e:
            logger.warning(f"[VWAP2.0] 앵커드 VWAP 계산 실패: {e}")
            return None

    def _calc_rsi(self, close: pd.Series, period: int = 14) -> float:
        try:
            delta  = close.diff()
            gain   = delta.where(delta > 0, 0.0).rolling(period).mean()
            loss   = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
            rs     = gain / loss.replace(0, np.nan)
            rsi    = 100 - (100 / (1 + rs))
            return float(rsi.iloc[-1])
        except Exception:
            return 50.0

    def _check_entry(
        self,
        df: pd.DataFrame,
        avwaps: List[AnchoredVWAP],
        ctx: MarketContext,
        market: str,
    ) -> Optional[Signal]:
        current_price = float(df["close"].iloc[-1])
        rsi = self._calc_rsi(df["close"])

        # RSI 과매도 확인
        if not (self.MIN_RSI_OVERSOLD <= rsi <= self.MAX_RSI_OVERSOLD):
            return None

        best_signal = None
        best_conf   = 0.0

        for avwap in avwaps:
            # lower2 밴드 터치 확인
            near_lower2 = abs(current_price - avwap.lower2) / avwap.lower2 < 0.008
            if not near_lower2:
                continue

            # 거래량 감소 확인 (매도 소진)
            recent_vol  = float(df["volume"].iloc[-3:].mean())
            avg_vol     = float(df["volume"].rolling(20).mean().iloc[-1])
            vol_declining = recent_vol < avg_vol * 0.8

            # VWAP까지 거리 (목표 수익률)
            potential_return = (avwap.vwap - current_price) / current_price
            if potential_return < 0.005:
                continue

            # 레짐 보정
            regime_bonus = 0.15 if ctx.regime == "RANGING" else 0.05

            confidence = min(
                0.35
                + avwap.strength * 0.2
                + (0.1 if vol_declining else 0.0)
                + regime_bonus
                + (0.05 if ctx.is_korean_session or ctx.is_us_session else 0.0)
                + (1 - rsi / 100) * 0.15,
                1.0,
            )

            if confidence > best_conf and confidence >= self.MIN_CONFIDENCE:
                best_conf   = confidence
                best_signal = Signal(
                    signal_type=SignalType.BUY,
                    confidence=confidence,
                    strategy_name=self.NAME,
                    metadata={
                        "anchor_type":      avwap.anchor_type,
                        "anchor_price":     avwap.anchor_price,
                        "vwap":             avwap.vwap,
                        "lower2":           avwap.lower2,
                        "target":           avwap.vwap,
                        "potential_return": potential_return,
                        "rsi":              rsi,
                        "vol_declining":    vol_declining,
                        "regime":           ctx.regime,
                    },
                )
                logger.info(
                    f"[VWAP2.0] 📊 {market} 앵커드VWAP lower2 터치 | "
                    f"앵커={avwap.anchor_type} | "
                    f"VWAP={avwap.vwap:.0f} | "
                    f"lower2={avwap.lower2:.0f} | "
                    f"RSI={rsi:.1f} | "
                    f"목표수익={potential_return:.2%} | "
                    f"신뢰도={confidence:.2f}"
                )

        return best_signal
