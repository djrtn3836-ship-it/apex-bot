"""
APEX BOT - 거래량 스파이크 감지 (Volume Spike Detector)
이상 거래량 발생 전후 신호 포착 → 선행 진입 or 이탈 경고

감지 로직:
  1. 거래량이 20봉 평균 대비 N배 초과 → 스파이크 감지
  2. 스파이크 + 가격 상승 → 강한 매수 신호 보정
  3. 스파이크 + 가격 하락 → 덤핑 경고 (매수 차단)
  4. 연속 스파이크 (2봉 이상) → 추세 전환 신호
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from loguru import logger


@dataclass
class VolumeSpike:
    """감지된 거래량 스파이크"""
    market: str
    ratio: float         # 평균 대비 배수
    direction: str       # "UP" / "DOWN" / "NEUTRAL"
    price_change: float  # 동시 가격 변화율
    strength: float      # 0.0 ~ 1.0


class VolumeSpikeDetector:
    """
    거래량 스파이크 감지 및 신호 보정기

    사용법:
        vsd = VolumeSpikeDetector()

        # 전략 신호 생성 후
        spike = vsd.detect(df, market)
        if spike:
            adj = vsd.get_confidence_adjustment(spike)
            signal.confidence += adj
    """

    # 스파이크 임계값
    SPIKE_THRESHOLD  = 2.0   # 평균 × 2.0 이상 → 스파이크
    STRONG_THRESHOLD = 3.5   # 평균 × 3.5 이상 → 강한 스파이크
    EXTREME_THRESHOLD = 6.0  # 평균 × 6.0 이상 → 극단 스파이크

    # 이동 평균 기간
    MA_PERIOD = 20

    def __init__(self,
                 spike_threshold: float = SPIKE_THRESHOLD,
                 ma_period: int = MA_PERIOD):
        self._threshold = spike_threshold
        self._ma_period = ma_period
        self._spike_history: Dict[str, List[VolumeSpike]] = {}

    def detect(
        self,
        df: pd.DataFrame,
        market: str,
        lookback: int = 3,
    ) -> Optional[VolumeSpike]:
        """
        DataFrame에서 최근 N봉 스파이크 감지

        Args:
            df:       OHLCV DataFrame (volume 컬럼 필요)
            market:   마켓 코드
            lookback: 최근 N봉 스캔

        Returns:
            VolumeSpike 또는 None
        """
        if df is None or len(df) < self._ma_period + lookback:
            return None

        if "volume" not in df.columns:
            return None

        vol = df["volume"].values
        close = df["close"].values

        # 이동 평균 거래량 (최근 봉 제외)
        ma_vol = vol[-(self._ma_period + lookback):-lookback].mean()
        if ma_vol <= 0:
            return None

        # 최근 봉들 스캔
        for i in range(lookback, 0, -1):
            current_vol = vol[-i]
            ratio = current_vol / ma_vol

            if ratio >= self._threshold:
                # 방향 판단
                if len(close) > i:
                    price_change = (close[-i] - close[-i-1]) / close[-i-1]
                else:
                    price_change = 0.0

                if price_change > 0.005:
                    direction = "UP"
                elif price_change < -0.005:
                    direction = "DOWN"
                else:
                    direction = "NEUTRAL"

                strength = min((ratio - self._threshold) / (self.EXTREME_THRESHOLD - self._threshold), 1.0)

                spike = VolumeSpike(
                    market=market,
                    ratio=ratio,
                    direction=direction,
                    price_change=price_change,
                    strength=strength,
                )

                # 히스토리 저장
                self._spike_history.setdefault(market, [])
                self._spike_history[market].append(spike)
                if len(self._spike_history[market]) > 10:
                    self._spike_history[market] = self._spike_history[market][-10:]

                logger.debug(
                    f"⚡ 거래량 스파이크 | {market} | "
                    f"비율={ratio:.1f}x | 방향={direction} | "
                    f"가격변화={price_change:.2%}"
                )
                return spike

        return None

    def get_confidence_adjustment(self, spike: Optional[VolumeSpike]) -> float:
        """
        스파이크 기반 신뢰도 보정값 반환

        Returns:
            -0.20 ~ +0.20 범위
        """
        if spike is None:
            return 0.0

        base = spike.strength * 0.15  # 최대 +0.15

        if spike.direction == "UP":
            # 거래량 + 상승 → 신뢰도 증가
            return base
        elif spike.direction == "DOWN":
            # 거래량 + 하락 → 신뢰도 감소 (덤핑 경고)
            return -base * 1.5  # 패널티 더 강하게
        else:
            # NEUTRAL
            return base * 0.3

    def is_dumping(self, df: pd.DataFrame, market: str) -> Tuple[bool, str]:
        """
        덤핑(대량 매도) 감지

        Returns:
            (덤핑 여부, 사유)
        """
        spike = self.detect(df, market, lookback=2)
        if spike and spike.direction == "DOWN":
            if spike.ratio >= self.STRONG_THRESHOLD:
                return (
                    True,
                    f"{market} 대량 매도 감지 ({spike.ratio:.1f}x, {spike.price_change:.2%})"
                )
        return False, "OK"

    def is_breakout(self, df: pd.DataFrame, market: str) -> Tuple[bool, float]:
        """
        거래량 돌파 감지 (매수 신호 보강)

        Returns:
            (돌파 여부, 신뢰도 보정값)
        """
        spike = self.detect(df, market, lookback=2)
        if spike and spike.direction == "UP":
            adj = self.get_confidence_adjustment(spike)
            return True, adj
        return False, 0.0

    def get_volume_ratio(self, df: pd.DataFrame) -> float:
        """현재 거래량 / 평균 거래량 비율"""
        if df is None or len(df) < self._ma_period + 1:
            return 1.0
        vol = df["volume"].values
        ma = vol[-(self._ma_period+1):-1].mean()
        if ma <= 0:
            return 1.0
        return vol[-1] / ma

    def get_spike_history(self, market: str) -> List[VolumeSpike]:
        return self._spike_history.get(market, [])
