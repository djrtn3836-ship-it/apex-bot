"""APEX BOT -    (Abstract Base Class)"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional
import pandas as pd
import logging

logger = logging.getLogger(__name__)


class SignalType(Enum):
    BUY  = 1
    HOLD = 0
    SELL = -1


@dataclass
class StrategySignal:
    """StrategySignal 클래스"""
    strategy_name: str
    market: str
    signal: SignalType
    score: float            # 신호 강도 (-1.0 ~ +1.0)
    confidence: float       # 신뢰도 (0.0 ~ 1.0)
    entry_price: float
    stop_loss: float
    take_profit: float
    reason: str             # 신호 발생 근거
    timeframe: str
    timestamp: datetime
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

    @property
    def is_buy(self) -> bool:
        return self.signal == SignalType.BUY

    @property
    def is_sell(self) -> bool:
        return self.signal == SignalType.SELL

    @property
    def weighted_score(self) -> float:
        """( × )"""
        return self.score * self.confidence

    # ── 하위 호환 속성 (signal_type, strength) ──────────────────
    @property
    def signal_type(self) -> 'SignalType':
        """: signal"""
        return self.signal

    @property
    def strength(self) -> float:
        """: score"""
        return self.score


# ── 하위 호환 별칭 ─────────────────────────────────────────────
Signal = StrategySignal


class BaseStrategy(ABC):
    """BaseStrategy 클래스"""

    # 전략 메타데이터 (하위 클래스에서 오버라이드)
    NAME: str = "base"
    DESCRIPTION: str = ""
    WEIGHT: float = 1.0                  # 앙상블 가중치
    MIN_CANDLES: int = 200               # 최소 필요 캔들 수
    SUPPORTED_TIMEFRAMES: list = ["60"]  # 지원 타임프레임

    def __init__(self, params: dict = None):
        self.params = params or self._default_params()
        self._last_signal: Optional[StrategySignal] = None
        self._signal_count = 0
        self._enabled = True

    @abstractmethod
    def _default_params(self) -> dict:
        """_default_params 실행"""
        return {}

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame, market: str,
                        timeframe: str = "60") -> Optional[StrategySignal]:
        """( )
        
        Args:
            df: OHLCV +  DataFrame (calculate_all_indicators  )
            market:   (: "KRW-BTC")
            timeframe:  ( )
        
        Returns:
            StrategySignal  None ( )"""
        pass

    def validate_df(self, df: pd.DataFrame) -> bool:
        """DataFrame"""
        if df is None or df.empty:
            return False
        if len(df) < self.MIN_CANDLES:
            logger.debug(f" {self.NAME}:   ({len(df)} < {self.MIN_CANDLES})")
            return False
        required_cols = ["open", "high", "low", "close", "volume"]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            logger.warning(f" {self.NAME}:    {missing}")
            return False
        return True

    def _create_signal(
        self,
        signal: SignalType,
        score: float,
        confidence: float,
        market: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        reason: str,
        timeframe: str,
        metadata: dict = None
    ) -> StrategySignal:
        """구현부"""
        self._signal_count += 1
        sig = StrategySignal(
            strategy_name=self.NAME,
            market=market,
            signal=signal,
            score=score,
            confidence=min(max(confidence, 0.0), 1.0),
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reason=reason,
            timeframe=timeframe,
            timestamp=datetime.now(),
            metadata=metadata or {},
        )
        self._last_signal = sig
        logger.info(
            f" [{self.NAME}] {signal.name}  | {market} | "
            f": {score:.2f} | : {confidence:.2%} | {reason}"
        )
        return sig

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self):
        self._enabled = True

    def disable(self):
        self._enabled = False

    def get_stats(self) -> dict:
        return {
            "name": self.NAME,
            "enabled": self._enabled,
            "weight": self.WEIGHT,
            "signal_count": self._signal_count,
            "last_signal": self._last_signal.signal.name if self._last_signal else None,
        }

    # ── 하위 호환 속성 & 메서드 ───────────────────────────────────
    @property
    def name(self) -> str:
        """: NAME"""
        return self.NAME

    @property
    def weight(self) -> float:
        """: WEIGHT"""
        return self.WEIGHT

    def get_parameters(self) -> dict:
        """: _default_params()  +  params"""
        return self.params.copy()

    def analyze(self, market: str, df: pd.DataFrame,
                timeframe: str = "60") -> Optional[StrategySignal]:
        """: generate_signal()"""
        return self.generate_signal(df, market, timeframe)


def safe_float(value, default: float = 0.0) -> float:
    """NaN/inf 방어 float 변환 — v2 전략 전용"""
    import math
    try:
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except (TypeError, ValueError):
        return default
