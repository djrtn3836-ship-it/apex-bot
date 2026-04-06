"""
APEX BOT - 전략 기본 클래스 (Abstract Base Class)
모든 전략이 상속하는 표준 인터페이스
"""
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
    """전략 신호 데이터"""
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
        """가중 점수 (신호값 × 신뢰도)"""
        return self.score * self.confidence

    # ── 하위 호환 속성 (signal_type, strength) ──────────────────
    @property
    def signal_type(self) -> 'SignalType':
        """하위 호환: signal 필드의 별칭"""
        return self.signal

    @property
    def strength(self) -> float:
        """하위 호환: score 필드의 별칭"""
        return self.score


# ── 하위 호환 별칭 ─────────────────────────────────────────────
Signal = StrategySignal


class BaseStrategy(ABC):
    """
    전략 추상 기본 클래스
    모든 전략 모듈이 이 클래스를 상속
    """

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
        """기본 파라미터 반환"""
        return {}

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame, market: str,
                        timeframe: str = "60") -> Optional[StrategySignal]:
        """
        신호 생성 (핵심 메서드)
        
        Args:
            df: OHLCV + 지표 DataFrame (calculate_all_indicators 적용 완료)
            market: 마켓 코드 (예: "KRW-BTC")
            timeframe: 타임프레임 (분 단위)
        
        Returns:
            StrategySignal 또는 None (신호 없음)
        """
        pass

    def validate_df(self, df: pd.DataFrame) -> bool:
        """DataFrame 유효성 검증"""
        if df is None or df.empty:
            return False
        if len(df) < self.MIN_CANDLES:
            logger.debug(f"⚠️ {self.NAME}: 캔들 부족 ({len(df)} < {self.MIN_CANDLES})")
            return False
        required_cols = ["open", "high", "low", "close", "volume"]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            logger.warning(f"⚠️ {self.NAME}: 필수 컬럼 누락 {missing}")
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
        """신호 객체 생성 헬퍼"""
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
            f"📊 [{self.NAME}] {signal.name} 신호 | {market} | "
            f"점수: {score:.2f} | 신뢰도: {confidence:.2%} | {reason}"
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
        """하위 호환: NAME 클래스 속성의 인스턴스 별칭"""
        return self.NAME

    @property
    def weight(self) -> float:
        """하위 호환: WEIGHT 클래스 속성의 인스턴스 별칭"""
        return self.WEIGHT

    def get_parameters(self) -> dict:
        """하위 호환: _default_params() 별칭 + 현재 params 반환"""
        return self.params.copy()

    def analyze(self, market: str, df: pd.DataFrame,
                timeframe: str = "60") -> Optional[StrategySignal]:
        """하위 호환: generate_signal() 의 인수 순서 교체 래퍼"""
        return self.generate_signal(df, market, timeframe)
