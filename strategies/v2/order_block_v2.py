from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from loguru import logger
from strategies.base_strategy import BaseStrategy, Signal, SignalType, safe_float, safe_last, safe_rolling_mean, safe_rolling_std, safe_div, kst_now
from strategies.v2.context.market_context import MarketContextEngine, MarketContext

# [PHASE2-D] settings SL 참조
try:
    from config.settings import Settings as _SettingsClass; _SETTINGS = _SettingsClass()
except Exception:
    class _SettingsClass:
        STRATEGY_SL_RATIO = {"DEFAULT": 0.017}
        STRATEGY_TP_RATIO = {"DEFAULT": 0.045}
    _SETTINGS = _SettingsClass()


@dataclass
class OrderBlock:
    ob_type: str          # "bullish" / "bearish"
    high: float
    low: float
    mid: float
    candle_idx: int
    strength: float       # 0~1
    impulse_atr: float
    volume_spike: float   # 거래량 급증 배수
    time_weight: float    # 시간대 가중치 (기관 시간대 2.0)
    touch_count: int = 0  # 터치 횟수 (3회 이상 무효화)
    confirmed: bool = False


class OrderBlockStrategy2(BaseStrategy):
    """
    OrderBlock 2.0 — 기관 발자국 추적기
    거래량 프로파일 + 캔들 패턴 + 시간대 분석 결합
    """
    NAME        = "OrderBlock_SMC"
    BASE_CONF   = 0.48   # 기본 신뢰도 — config min_confidence(Order_Block)
    DESCRIPTION = "기관 오더블록 2.0 — 거래량+시간대+다중터치 필터"
    VERSION     = "2.0"

    # 파라미터
    IMPULSE_ATR_MULT = 1.8   # 충격 이동 ATR 배수 기준
    VOLUME_SPIKE_MULT  = 1.5   # 거래량 급증 배수 기준
    MAX_TOUCH_COUNT    = 2     # 최대 터치 횟수 (3회부터 무효화)
    OB_ZONE_TOLERANCE  = 0.003 # OB 구간 인식 허용 오차 0.3%
    ENTRY_SPLIT        = True  # 분할 진입 여부
    MIN_VOLUME_RANK    = 0.3   # 최소 거래량 순위

    def _default_params(self) -> dict:
        return {"impulse_atr_mult": 2.5, "volume_spike_mult": 2.0, "max_touch_count": 2}

    def __init__(self):
        super().__init__()
        self._context_engine = MarketContextEngine()
        self._ob_cache: dict  = {}  # market -> List[OrderBlock]

    def generate_signal(self, df: pd.DataFrame, market: str = "") -> Optional[Signal]:
        try:
            if not self._enabled:
                return None
            if df is None or len(df) < 50:
                return None

            ctx = self._context_engine.analyze(df, market)

            # 하락 추세에서는 강세 OB 진입 금지
            if ctx.regime == "TRENDING_DOWN" and ctx.volatility_rank > 0.7:
                return None

            obs = self._detect_order_blocks(df, ctx)
            if not obs:
                return None

            signal = self._check_ob_entry(df, obs, ctx, market)
            return signal

        except Exception as e:
            logger.warning(f"[OB2.0] {market} 오류: {e}")
            return None

    def _detect_order_blocks(self, df: pd.DataFrame, ctx: MarketContext) -> List[OrderBlock]:
        obs: List[OrderBlock] = []
        close    = df["close"].values
        high     = df["high"].values
        low      = df["low"].values
        volume   = df["volume"].values
        open_arr = df["open"].values  # [BUG-REAL-4 FIX]

        avg_vol = np.mean(volume[-20:]) if len(volume) >= 20 else np.mean(volume)
        atr     = self._calc_atr(high, low, close, 14)

        for i in range(3, len(df) - 1):
            # 충격 이동 감지 (다음 캔들이 ATR * 배수 이상 이동)
            # 현재 봉 자체 이동폭 (고가-저가) + 전봉 대비 변화폭 중 큰 값
            body_move = abs(close[i] - open_arr[i])  # [BUG-REAL-4 FIX]
            gap_move  = abs(close[i] - close[i-1]) if i > 0 else 0
            next_move = max(body_move, gap_move)
            if next_move < atr * self.IMPULSE_ATR_MULT:
                continue

            # 거래량 급증 확인
            vol_spike = volume[i] / avg_vol if avg_vol > 0 else 0
            if vol_spike < self.VOLUME_SPIKE_MULT:
                continue

            # 시간대 가중치
            time_w = 2.0 if (ctx.is_korean_session or ctx.is_us_session) else 1.0

            # 강세 OB: 임펄스 봉 후 상승 이탈 (음봉/양봉 무관)
            # 조건: 다음 봉이 현재 봉 고가의 99% 이상 또는 전체 이동이 상승
            next_close = close[i+1] if i+1 < len(close) else close[i]
            next_high  = high[i+1]  if i+1 < len(high)  else high[i]
            is_bullish_impulse = (
                next_close > close[i] * 1.005  # 다음봉 0.5% 이상 상승
                or next_high > high[i]           # 또는 다음봉 고가 초과
            )
            is_bearish_impulse = (
                next_close < close[i] * 0.995   # 다음봉 0.5% 이상 하락
                or (close[i+1] if i+1 < len(close) else close[i]) < low[i]
            )

            if is_bullish_impulse:
                strength = min(vol_spike / 2.5, 1.0) * time_w * 0.5
                obs.append(OrderBlock(
                    ob_type="bullish",
                    high=high[i],
                    low=low[i],
                    mid=(high[i] + low[i]) / 2,
                    candle_idx=i,
                    strength=min(strength, 1.0),
                    impulse_atr=safe_div(next_move, atr),
                    volume_spike=vol_spike,
                    time_weight=time_w,
                ))
            elif is_bearish_impulse:
                strength = min(vol_spike / 2.5, 1.0) * time_w * 0.5
                obs.append(OrderBlock(
                    ob_type="bearish",
                    high=high[i],
                    low=low[i],
                    mid=(high[i] + low[i]) / 2,
                    candle_idx=i,
                    strength=min(strength, 1.0),
                    impulse_atr=safe_div(next_move, atr),
                    volume_spike=vol_spike,
                    time_weight=time_w,
                ))

        # 최근 OB만 유지 (최대 10개)
        return sorted(obs, key=lambda x: x.candle_idx, reverse=True)[:10]

    def _check_ob_entry(
        self,
        df: pd.DataFrame,
        obs: List[OrderBlock],
        ctx: MarketContext,
        market: str,
    ) -> Optional[Signal]:
        current_price = safe_last(df["close"])
        current_vol   = safe_last(df["volume"])
        avg_vol       = safe_last(safe_rolling_mean(df["volume"], 20))

        for ob in obs:
            if ob.ob_type != "bullish":
                continue
            if ob.touch_count >= self.MAX_TOUCH_COUNT:
                continue

            # 가격이 OB 구간 안에 있는지 확인
            in_zone = ob.low * (1 - self.OB_ZONE_TOLERANCE) <= current_price <= ob.high * (1 + self.OB_ZONE_TOLERANCE)
            if not in_zone:
                continue

            # 거래량 확인
            if ctx.volume_rank < self.MIN_VOLUME_RANK:
                continue

            ob.touch_count += 1

            # 분할 진입 (1차: 50%, 2차: 100%)
            size_mult = 0.5 if ob.touch_count == 1 else 1.0

            confidence = ob.strength * (1 + ctx.volume_rank) * size_mult
            confidence = min(confidence, 1.0)

            if confidence < 0.35:
                continue

            logger.info(
                f"[OB2.0] 📦 {market} 강세OB 진입 | "
                f"구간={ob.low:.0f}~{ob.high:.0f} | "
                f"강도={ob.strength:.2f} | "
                f"터치={ob.touch_count}회 | "
                f"신뢰도={confidence:.2f}"
            )

            return Signal(
                signal=SignalType.BUY,
                confidence=confidence,
                strategy_name=self.NAME,
            market         = market,
            score          = confidence * 2.0 - 1.0,
            entry_price    = safe_last(df["close"]),
            stop_loss      = safe_last(df["close"]) * (1 - _SETTINGS.STRATEGY_SL_RATIO.get("OrderBlock_SMC", 0.017)),
            take_profit    = safe_last(df["close"]) * 1.045,
            reason         = f"{self.NAME} v2 신호",
            timeframe      = "1h",
            timestamp      = kst_now(),
                metadata={
                    "ob_low": ob.low,
                    "ob_high": ob.high,
                    "touch_count": ob.touch_count,
                    "vol_spike": ob.volume_spike,
                    "time_weight": ob.time_weight,
                    "size_multiplier": size_mult,
                },
            )
        return None

    def _calc_atr(self, high, low, close, period=14) -> float:
        try:
            h = pd.Series(high)
            l = pd.Series(low)
            c = pd.Series(close)
            tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
            return float(safe_last(safe_rolling_mean(tr, period)))
        except Exception as _e:
            return float(np.mean(high - low))