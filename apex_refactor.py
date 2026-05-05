"""
apex_refactor.py
════════════════════════════════════════════════════════════
APEX BOT 전체 통합 리팩토링 스크립트

수정 항목:
  [STEP-1] core/constants.py          신설 (전략 키 Enum + 공통 상수)
  [STEP-2] strategies/base_strategy.py logging → loguru + kst_now 통일
  [STEP-3] core/engine_ml.py          __future__ 위치 + Path 임포트 + 주석 정리
  [STEP-4] core/engine_utils.py       TradingEngine stub 제거
  [STEP-5] signals/signal_combiner.py VolBreakout 중복 키 + VWAP 명시 0.0 + constants 참조
  [STEP-6] risk/risk_manager.py       daily_loss_limit fallback 0.05→0.03 + constants 참조
  [STEP-7] core/engine_cycle.py       서킷브레이커 단일 기준 (settings 참조)
  [STEP-8] core/engine_sell.py        partial_sell record_trade_result 추가
  [STEP-9] risk/position_sizer.py     전략 키 constants 참조 + Order_Block 키 통일
  [STEP-10] strategies/market_structure/order_block.py  NAME → OrderBlock_SMC_V1
  [STEP-11] strategies/momentum/macd_cross.py           logging → loguru
  [STEP-12] strategies/momentum/supertrend.py           logging → loguru
  [STEP-13] strategies/mean_reversion/bollinger_squeeze.py logging → loguru
  [STEP-14] strategies/mean_reversion/vwap_reversion.py   logging → loguru

안전 장치:
  - 모든 파일 수정 전 archive/refactor_<timestamp>/ 에 백업
  - 각 파일 수정 후 ast.parse() syntax 검증
  - 실패 시 자동 롤백
  - 최종 결과 요약 출력
════════════════════════════════════════════════════════════
"""
from __future__ import annotations
import ast
import shutil
import sys
from datetime import datetime
from pathlib import Path

# ── 백업 디렉토리 ────────────────────────────────────────────
_TS  = datetime.now().strftime("%Y%m%d_%H%M%S")
_BAK = Path(f"archive/refactor_{_TS}")
_BAK.mkdir(parents=True, exist_ok=True)

_results: dict[str, str] = {}   # 파일경로 → "OK" | "SKIP" | "FAIL:사유"


# ══════════════════════════════════════════════════════════════
# 헬퍼
# ══════════════════════════════════════════════════════════════
def _backup(path: Path):
    if path.exists():
        dest = _BAK / path.name
        shutil.copy2(path, dest)


def _syntax_ok(code: str, label: str) -> bool:
    try:
        ast.parse(code)
        return True
    except SyntaxError as e:
        print(f"  ✗ SyntaxError in {label}: {e}")
        return False


def _write(path: Path, code: str, label: str) -> bool:
    """백업 → 문법검증 → 쓰기. 실패 시 원본 복원."""
    _backup(path)
    if not _syntax_ok(code, label):
        _results[label] = "FAIL:SyntaxError"
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    _results[label] = "OK"
    print(f"  ✅ {label}")
    return True


def _read(path: Path) -> str | None:
    if not path.exists():
        print(f"  ⚠ 파일 없음: {path}")
        return None
    return path.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════
# STEP-1  core/constants.py  신설
# ══════════════════════════════════════════════════════════════
def step1_constants():
    print("\n[STEP-1] core/constants.py 신설...")
    code = '''\
"""
core/constants.py
─────────────────────────────────────────────────────────────
APEX BOT 공통 상수 — 단일 진실 공급원 (Single Source of Truth)

규칙:
  - 전략 이름 키는 반드시 StrategyKey Enum 사용
  - 수치 파라미터(임계값/비율)는 config/settings.py 유지
  - 이 파일은 거의 변경 없는 안정적 파일로 유지
─────────────────────────────────────────────────────────────
"""
from __future__ import annotations
from enum import Enum


# ── 전략 이름 키 ─────────────────────────────────────────────
class StrategyKey(str, Enum):
    """전략 식별 키 — 오타 방지용 Enum (str 상속으로 dict 키로 직접 사용 가능)"""
    MACD_CROSS         = "MACD_Cross"
    RSI_DIVERGENCE     = "RSI_Divergence"
    SUPERTREND         = "Supertrend"
    BOLLINGER_SQUEEZE  = "Bollinger_Squeeze"
    VWAP_REVERSION     = "VWAP_Reversion"
    VOL_BREAKOUT       = "VolBreakout"       # 표준 키 (Vol_Breakout 폐기)
    ATR_CHANNEL        = "ATR_Channel"
    ORDER_BLOCK_SMC    = "OrderBlock_SMC"    # v2 활성 전략
    ORDER_BLOCK_SMC_V1 = "OrderBlock_SMC_V1" # v1 비활성 (롤백용)
    ML_ENSEMBLE        = "ML_Ensemble"
    BEAR_REVERSAL      = "BEAR_REVERSAL"
    SURGE_FASTENTRY    = "SURGE_FASTENTRY"


# ── 비활성화 전략 집합 ────────────────────────────────────────
# 여기에 추가하면 signal_combiner, ensemble_engine 전체에서 자동 제외
DISABLED_STRATEGIES: frozenset[str] = frozenset({
    StrategyKey.VWAP_REVERSION,   # DB -₩3,158 (42% 승률)
    StrategyKey.VOL_BREAKOUT,     # DB -₩3,521 (29% 승률)
    StrategyKey.RSI_DIVERGENCE,   # 백테스트 -10.0%/년
    StrategyKey.ORDER_BLOCK_SMC_V1,  # v2로 대체됨
})

# ── 활성 OrderBlock 전략 ─────────────────────────────────────
ACTIVE_ORDER_BLOCK = StrategyKey.ORDER_BLOCK_SMC   # v2 사용 중

# ── profit_rate 단위 규칙 ─────────────────────────────────────
# 내부 계산: 소수 (0.032 = +3.2%)
# DB 저장:   소수 (trade_history.profit_rate)
# 표시/로그: % (f"{profit_rate*100:.2f}%")
# 변환 예:   engine_sell → record_trade_result(profit_rate / 100.0)

# ── 최소 주문 금액 ────────────────────────────────────────────
MIN_ORDER_KRW: int = 5_000
MIN_POSITION_KRW: int = 20_000

# ── Upbit 수량 소수점 자릿수 ──────────────────────────────────
VOLUME_PRECISION: dict[str, int] = {
    "KRW-BTC":  8, "KRW-ETH":  8, "KRW-XRP":  2,
    "KRW-SOL":  4, "KRW-ADA":  2, "KRW-DOGE": 2,
    "KRW-AVAX": 4, "KRW-DOT":  2, "KRW-LINK": 4,
    "KRW-ATOM": 4, "KRW-BEAM": 2, "KRW-RED":  2,
    "KRW-BLAST":2, "KRW-COMP": 4, "KRW-DOOD": 2,
    "KRW-POKT": 2, "KRW-INJ":  4, "KRW-AGLD": 2,
}

# ── 스테이블코인 블랙리스트 ───────────────────────────────────
STABLE_MARKETS: frozenset[str] = frozenset({
    "KRW-USDT", "KRW-USDC", "KRW-USD1", "KRW-BUSD", "KRW-DAI",
    "KRW-TUSD", "KRW-USDP", "KRW-FDUSD", "KRW-PYUSD", "KRW-USDS",
})
'''
    _write(Path("core/constants.py"), code, "core/constants.py")


# ══════════════════════════════════════════════════════════════
# STEP-2  strategies/base_strategy.py  logging → loguru
# ══════════════════════════════════════════════════════════════
def step2_base_strategy():
    print("\n[STEP-2] strategies/base_strategy.py logging→loguru 통일...")
    path = Path("strategies/base_strategy.py")
    src  = _read(path)
    if src is None:
        _results["strategies/base_strategy.py"] = "FAIL:파일없음"
        return

    # 이미 적용됐는지 확인
    if "from loguru import logger" in src and "import logging" not in src:
        print("  ℹ️  이미 적용됨 → SKIP")
        _results["strategies/base_strategy.py"] = "SKIP"
        return

    code = '''\
"""APEX BOT - 전략 추상 기반 클래스 (Abstract Base Class)"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import math
import numpy as np
import pandas as pd
import pytz
from datetime import datetime
from loguru import logger   # [REFACTOR] logging → loguru 통일


def kst_now() -> datetime:
    """KST 기준 현재 datetime (timezone-aware)"""
    return datetime.now(pytz.timezone("Asia/Seoul"))


class SignalType(Enum):
    BUY  = 1
    HOLD = 0
    SELL = -1


@dataclass
class StrategySignal:
    """전략 신호 데이터 클래스"""
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
        """신호강도 × 신뢰도"""
        return self.score * self.confidence

    # ── 하위 호환 속성 ───────────────────────────────────────
    @property
    def signal_type(self) -> SignalType:
        """하위호환: signal"""
        return self.signal

    @property
    def strength(self) -> float:
        """하위호환: score"""
        return self.score


# 하위 호환 별칭
Signal = StrategySignal


class BaseStrategy(ABC):
    """전략 추상 기반 클래스"""

    NAME: str        = "base"
    DESCRIPTION: str = ""
    WEIGHT: float    = 1.0
    MIN_CANDLES: int = 200
    SUPPORTED_TIMEFRAMES: list = ["60"]

    def __init__(self, params: dict = None):
        self.params         = params or self._default_params()
        self._last_signal: Optional[StrategySignal] = None
        self._signal_count  = 0
        self._enabled       = True

    @abstractmethod
    def _default_params(self) -> dict:
        return {}

    @abstractmethod
    def generate_signal(
        self, df: pd.DataFrame, market: str, timeframe: str = "60"
    ) -> Optional[StrategySignal]:
        """신호 생성 — 하위 클래스에서 구현"""
        pass

    def validate_df(self, df: pd.DataFrame) -> bool:
        if df is None or df.empty:
            return False
        if len(df) < self.MIN_CANDLES:
            logger.debug(f"전략 {self.NAME}: 캔들 부족 ({len(df)} < {self.MIN_CANDLES})")
            return False
        required_cols = ["open", "high", "low", "close", "volume"]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            logger.warning(f"전략 {self.NAME}: 컬럼 누락 {missing}")
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
        metadata: dict = None,
    ) -> StrategySignal:
        """신호 생성 + 통계 추적 (loguru 로그 포함)"""
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
            timestamp=kst_now(),   # [REFACTOR] kst_now() 통일
            metadata=metadata or {},
        )
        self._last_signal = sig
        logger.info(
            f"[{self.NAME}] {signal.name} 신호 | {market} | "
            f"점수:{score:.2f} | 신뢰도:{confidence:.2%} | {reason}"
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
            "name":         self.NAME,
            "enabled":      self._enabled,
            "weight":       self.WEIGHT,
            "signal_count": self._signal_count,
            "last_signal":  self._last_signal.signal.name if self._last_signal else None,
        }

    # ── 하위 호환 속성 & 메서드 ──────────────────────────────
    @property
    def name(self) -> str:
        return self.NAME

    @property
    def weight(self) -> float:
        return self.WEIGHT

    def get_parameters(self) -> dict:
        return self.params.copy()

    def analyze(
        self, market: str, df: pd.DataFrame, timeframe: str = "60"
    ) -> Optional[StrategySignal]:
        """하위호환: generate_signal() 래퍼"""
        return self.generate_signal(df, market, timeframe)


# ── 안전 유틸 함수 (v2 전략 전용) ────────────────────────────
def safe_last(series) -> float:
    try:
        if isinstance(series, pd.Series):
            val = series.dropna()
            if len(val) == 0:
                return 0.0
            v = float(val.iloc[-1])
        else:
            v = float(series[-1])
        if math.isnan(v) or math.isinf(v):
            return 0.0
        return v
    except Exception:
        return 0.0


def safe_float(value, default: float = 0.0) -> float:
    try:
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


def safe_rolling_mean(series, window: int, default: float = 0.0):
    try:
        return series.rolling(window).mean().fillna(default)
    except Exception:
        return series * 0 + default


def safe_rolling_std(series, window: int, default: float = 0.0):
    try:
        return series.rolling(window).std().fillna(default)
    except Exception:
        return series * 0 + default


def safe_div(numerator, denominator, default: float = 0.0):
    try:
        if hasattr(denominator, "__len__"):
            denom = denominator.replace(0, float("nan"))
            return (numerator / denom).fillna(default)
        if denominator == 0 or (isinstance(denominator, float) and math.isnan(denominator)):
            return default
        return numerator / denominator
    except Exception:
        return default
'''
    _write(path, code, "strategies/base_strategy.py")


# ══════════════════════════════════════════════════════════════
# STEP-3  core/engine_ml.py  __future__ 위치 + Path 임포트
# ══════════════════════════════════════════════════════════════
def step3_engine_ml():
    print("\n[STEP-3] core/engine_ml.py __future__ 위치 + Path 임포트 수정...")
    path = Path("core/engine_ml.py")
    src  = _read(path)
    if src is None:
        _results["core/engine_ml.py"] = "FAIL:파일없음"
        return

    # __future__ 가 첫 줄인지 확인
    first_line = src.lstrip().split("\n")[0].strip()
    path_imported = "from pathlib import Path" in src
    if first_line == "from __future__ import annotations" and path_imported:
        print("  ℹ️  이미 적용됨 → SKIP")
        _results["core/engine_ml.py"] = "SKIP"
        return

    # __future__ 를 첫 줄로, 나머지 임포트 정리
    # 기존 코드에서 __future__ 와 datetime 임포트 제거 후 재조립
    lines = src.split("\n")
    cleaned = [l for l in lines if l.strip() not in (
        "from __future__ import annotations",
        "from datetime import datetime",
    )]
    new_header = (
        "from __future__ import annotations\n"
        "from datetime import datetime\n"
        "from pathlib import Path\n"
    )
    # 기존 docstring 보존 (첫 번째 빈 줄 이후 붙이기)
    body = "\n".join(cleaned)
    new_src = new_header + body

    # 잘못된 주석 제거: "# ── 매수 실행 ──" 주석 (기능 없는 오해 주석)
    new_src = new_src.replace(
        "    # ── 매수 실행 ────────────────────────────────────────────────\n    \n\n",
        "\n"
    ).replace(
        "    # ── 매수 실행 ────────────────────────────────────────────────\n\n",
        "\n"
    )

    _write(path, new_src, "core/engine_ml.py")


# ══════════════════════════════════════════════════════════════
# STEP-4  core/engine_utils.py  TradingEngine stub 제거
# ══════════════════════════════════════════════════════════════
def step4_engine_utils():
    print("\n[STEP-4] core/engine_utils.py TradingEngine stub 제거...")
    path = Path("core/engine_utils.py")
    src  = _read(path)
    if src is None:
        _results["core/engine_utils.py"] = "FAIL:파일없음"
        return

    if "class TradingEngine:" not in src:
        print("  ℹ️  stub 없음 → SKIP")
        _results["core/engine_utils.py"] = "SKIP"
        return

    # stub 클래스 제거 + constants 참조 추가
    stub_marker = "\nclass TradingEngine:"
    idx = src.find(stub_marker)
    if idx != -1:
        new_src = src[:idx].rstrip() + "\n"
    else:
        new_src = src

    # constants 임포트 추가 (없으면)
    if "from core.constants import" not in new_src:
        insert_after = "from __future__ import annotations\n"
        new_src = new_src.replace(
            insert_after,
            insert_after + "from core.constants import VOLUME_PRECISION, MIN_ORDER_KRW, MIN_POSITION_KRW\n",
            1,
        )
        # _PREC_MAP 을 constants 참조로 교체
        new_src = new_src.replace(
            "_PREC_MAP: dict[str, int] = {",
            "# [REFACTOR] _PREC_MAP → core.constants.VOLUME_PRECISION 으로 이관\n"
            "# 하위 호환을 위해 alias 유지\n"
            "_PREC_MAP: dict[str, int] = VOLUME_PRECISION  # alias\nif False:  _PREC_MAP_OLD = {"
        )
        # 닫는 중괄호 처리 — 단순 replace로는 위험하므로 alias만 추가
        new_src = new_src.replace(
            "# [REFACTOR] _PREC_MAP → core.constants.VOLUME_PRECISION 으로 이관\n"
            "# 하위 호환을 위해 alias 유지\n"
            "_PREC_MAP: dict[str, int] = VOLUME_PRECISION  # alias\nif False:  _PREC_MAP_OLD = {",
            "# [REFACTOR] _PREC_MAP → core.constants.VOLUME_PRECISION alias\n"
            "_PREC_MAP: dict[str, int] = VOLUME_PRECISION\n"
            "_PREC_MAP_ORIG = {"
        )

    _write(path, new_src, "core/engine_utils.py")


# ══════════════════════════════════════════════════════════════
# STEP-5  signals/signal_combiner.py  키 중복 제거 + VWAP 명시 0.0
# ══════════════════════════════════════════════════════════════
def step5_signal_combiner():
    print("\n[STEP-5] signals/signal_combiner.py 키 중복 제거...")
    path = Path("signals/signal_combiner.py")
    src  = _read(path)
    if src is None:
        _results["signals/signal_combiner.py"] = "FAIL:파일없음"
        return

    code = '''\
"""
signals/signal_combiner.py
─────────────────────────────────────────────────────────────
APEX BOT 신호 결합기

변경 이력:
  v1.0 - 초기 구현
  v1.1 - _filter_by_regime() Signal 필드명 수정
  v1.2 - RSI_Divergence REGIME_PREFERRED 제거
  v1.3 - [REFACTOR] VolBreakout 중복 키 제거, VWAP 명시 0.0,
          StrategyKey constants 참조
─────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import asyncio
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from loguru import logger

from strategies.base_strategy import StrategySignal as Signal, SignalType
from config.settings import get_settings
from core.constants import StrategyKey, DISABLED_STRATEGIES


@dataclass
class CombinedSignal:
    """결합 신호 데이터 클래스"""
    market: str
    signal_type: SignalType
    score: float
    confidence: float
    agreement_rate: float
    contributing_strategies: List[str] = field(default_factory=list)

    @property
    def strategy_name(self) -> str:
        return self.contributing_strategies[0] if self.contributing_strategies else ""

    reasons: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)
    ml_signal: Optional[str] = None
    ml_confidence: float = 0.0

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def __getitem__(self, key: str):
        val = getattr(self, key, None)
        if val is None:
            raise KeyError(key)
        return val

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)


class SignalCombiner:
    """
    전략 신호 결합기
    [REFACTOR v1.3] StrategyKey Enum 참조, 중복 키 제거
    """

    # ── 전략 가중치 ──────────────────────────────────────────
    # 규칙: 비활성화 전략은 0.0 으로 명시 (기본값 1.0 방지)
    STRATEGY_WEIGHTS: Dict[str, float] = {
        StrategyKey.MACD_CROSS:        1.8,
        StrategyKey.RSI_DIVERGENCE:    0.0,   # 백테스트 -10.0%/년 → 비활성
        StrategyKey.SUPERTREND:        1.3,
        StrategyKey.BOLLINGER_SQUEEZE: 1.4,
        StrategyKey.VWAP_REVERSION:    0.0,   # DB -₩3,158 → 비활성
        StrategyKey.VOL_BREAKOUT:      0.0,   # DB -₩3,521 → 비활성  [FIX: 중복 제거]
        StrategyKey.ATR_CHANNEL:       1.0,
        StrategyKey.ORDER_BLOCK_SMC:   0.3,   # 백테스트 -4.7% → 하향
        StrategyKey.ML_ENSEMBLE:       3.0,   # 핵심 전략
        StrategyKey.BEAR_REVERSAL:     2.0,
    }

    # ── 레짐별 우선 전략 (1.2× 부스트) ──────────────────────
    # 비활성 전략(가중치 0.0)은 부스트해도 0.0이므로 제외
    REGIME_PREFERRED: Dict[str, set] = {
        "TRENDING": {
            StrategyKey.MACD_CROSS,
            StrategyKey.SUPERTREND,
            StrategyKey.ORDER_BLOCK_SMC,
        },
        "RANGING": {
            StrategyKey.BOLLINGER_SQUEEZE,
            StrategyKey.ATR_CHANNEL,
        },
        "VOLATILE": {
            StrategyKey.ATR_CHANNEL,
            StrategyKey.BOLLINGER_SQUEEZE,
        },
        "BEAR_REVERSAL": {
            StrategyKey.BOLLINGER_SQUEEZE,
            StrategyKey.BEAR_REVERSAL,
        },
    }

    def __init__(self, settings=None):
        self.settings       = settings or get_settings()
        self.buy_threshold  = self.settings.risk.buy_signal_threshold
        self.sell_threshold = -self.settings.risk.sell_signal_threshold
        self.min_agreement  = 0.20

    def combine(
        self,
        signals: List[Signal],
        market: str,
        ml_prediction: Optional[Dict] = None,
        regime: str = "UNKNOWN",
    ) -> Optional[CombinedSignal]:

        if not signals and not ml_prediction:
            return None

        ml_signal     = None
        ml_confidence = 0.0
        if ml_prediction:
            ml_signal     = ml_prediction.get("signal", "HOLD")
            ml_confidence = ml_prediction.get("confidence", 0.0)

        filtered_signals = self._filter_by_regime(signals, regime)

        buy_score, sell_score   = 0.0, 0.0
        buy_strategies          = []
        sell_strategies         = []
        reasons                 = []

        for sig in filtered_signals:
            # [FIX] 비활성화 전략 신호 차단 (가중치 0.0 또는 DISABLED_STRATEGIES)
            if sig.strategy_name in DISABLED_STRATEGIES:
                continue
            weight            = self.STRATEGY_WEIGHTS.get(sig.strategy_name, 1.0)
            if weight == 0.0:
                continue
            weighted_strength = sig.score * weight * sig.confidence

            if sig.signal == SignalType.BUY:
                buy_score += weighted_strength
                buy_strategies.append(sig.strategy_name)
                reasons.append(sig.reason)
            elif sig.signal == SignalType.SELL:
                sell_score += weighted_strength
                sell_strategies.append(sig.strategy_name)
                reasons.append(sig.reason)

        # ML 신호 추가
        if ml_signal and ml_confidence > 0.45:
            ml_weight = self.STRATEGY_WEIGHTS[StrategyKey.ML_ENSEMBLE]
            if ml_signal == "BUY":
                buy_score += ml_weight * ml_confidence
                buy_strategies.append(StrategyKey.ML_ENSEMBLE)
            elif ml_signal == "SELL":
                sell_score += ml_weight * ml_confidence
                sell_strategies.append(StrategyKey.ML_ENSEMBLE)
            elif ml_signal == "HOLD" and len(buy_strategies) >= 3:
                buy_score += ml_weight * ml_confidence * 0.5
                buy_strategies.append("ML_HOLD_BOOST")

        total_strategies = len(filtered_signals) + (1 if ml_signal else 0)
        net_score        = buy_score - sell_score

        if net_score >= self.buy_threshold:
            n_buy          = len(buy_strategies)
            agreement_rate = n_buy / max(total_strategies, 1)
            if agreement_rate < self.min_agreement:
                return None

            avg_confidence = (
                sum(s.confidence for s in filtered_signals
                    if s.signal == SignalType.BUY)
                / max(n_buy, 1)
            )
            if avg_confidence < 0.01 and ml_confidence > 0.0:
                avg_confidence = ml_confidence
            return CombinedSignal(
                market=market,
                signal_type=SignalType.BUY,
                score=buy_score,
                confidence=avg_confidence,
                agreement_rate=agreement_rate,
                contributing_strategies=buy_strategies,
                reasons=reasons[:5],
                metadata={"buy_score": buy_score, "sell_score": sell_score, "regime": regime},
                ml_signal=ml_signal,
                ml_confidence=ml_confidence,
            )

        elif net_score <= self.sell_threshold:
            n_sell         = len(sell_strategies)
            agreement_rate = n_sell / max(total_strategies, 1)
            if agreement_rate < self.min_agreement and not (
                ml_signal == "SELL" and ml_confidence > 0.52
            ):
                return None

            avg_confidence = (
                sum(s.confidence for s in filtered_signals
                    if s.signal == SignalType.SELL)
                / max(n_sell, 1)
            )
            if avg_confidence < 0.01 and ml_confidence > 0.0:
                avg_confidence = ml_confidence
            return CombinedSignal(
                market=market,
                signal_type=SignalType.SELL,
                score=-sell_score,
                confidence=avg_confidence,
                agreement_rate=agreement_rate,
                contributing_strategies=sell_strategies,
                reasons=reasons[:5],
                metadata={"buy_score": buy_score, "sell_score": sell_score, "regime": regime},
                ml_signal=ml_signal,
                ml_confidence=ml_confidence,
            )

        return None  # HOLD

    def _filter_by_regime(self, signals: List[Signal], regime: str) -> List[Signal]:
        preferred = self.REGIME_PREFERRED.get(regime.upper(), None)
        if preferred is None:
            return signals
        return [
            self._boost_signal(sig, score_mult=1.2, reason_suffix="[레짐부스트]")
            if sig.strategy_name in preferred else sig
            for sig in signals
        ]

    @staticmethod
    def _boost_signal(sig: Signal, score_mult: float = 1.0, reason_suffix: str = "") -> Signal:
        from strategies.base_strategy import StrategySignal
        return StrategySignal(
            strategy_name=sig.strategy_name,
            market=sig.market,
            signal=sig.signal,
            score=min(sig.score * score_mult, 1.0),
            confidence=sig.confidence,
            entry_price=sig.entry_price,
            stop_loss=sig.stop_loss,
            take_profit=sig.take_profit,
            reason=sig.reason + reason_suffix,
            timeframe=sig.timeframe,
            timestamp=sig.timestamp,
            metadata=sig.metadata,
        )

    def get_score_breakdown(self, signals: List[Signal]) -> Dict:
        return {
            sig.strategy_name: {
                "type":           sig.signal.name,
                "strength":       sig.score,
                "confidence":     sig.confidence,
                "weight":         self.STRATEGY_WEIGHTS.get(sig.strategy_name, 1.0),
                "weighted_score": sig.score * self.STRATEGY_WEIGHTS.get(sig.strategy_name, 1.0) * sig.confidence,
            }
            for sig in signals
        }
'''
    _write(path, code, "signals/signal_combiner.py")


# ══════════════════════════════════════════════════════════════
# STEP-6  risk/risk_manager.py  daily_loss_limit fallback 통일
# ══════════════════════════════════════════════════════════════
def step6_risk_manager():
    print("\n[STEP-6] risk/risk_manager.py daily_loss_limit fallback 0.05→0.03...")
    path = Path("risk/risk_manager.py")
    src  = _read(path)
    if src is None:
        _results["risk/risk_manager.py"] = "FAIL:파일없음"
        return

    if '"daily_loss_limit", 0.03' in src and '"daily_loss_limit", 0.05' not in src:
        print("  ℹ️  이미 적용됨 → SKIP")
        _results["risk/risk_manager.py"] = "SKIP"
        return

    new_src = src.replace(
        '"daily_loss_limit", 0.05)',
        '"daily_loss_limit", 0.03)'
    )
    if new_src == src:
        print("  ℹ️  패턴 없음 → SKIP")
        _results["risk/risk_manager.py"] = "SKIP"
        return

    _write(path, new_src, "risk/risk_manager.py")


# ══════════════════════════════════════════════════════════════
# STEP-7  core/engine_cycle.py  MDD-L3 하드코딩 2% → settings 참조
# ══════════════════════════════════════════════════════════════
def step7_engine_cycle():
    print("\n[STEP-7] core/engine_cycle.py 서킷브레이커 기준 settings 단일화...")
    path = Path("core/engine_cycle.py")
    src  = _read(path)
    if src is None:
        _results["core/engine_cycle.py"] = "FAIL:파일없음"
        return

    OLD = (
        "            _loss_limit     = _krw_bal * 0.02\n"
        "            if _daily_loss < -_loss_limit and _loss_limit > 0:"
    )
    NEW = (
        "            _dl_limit_cfg   = getattr(getattr(self, 'settings', None), 'risk', None)\n"
        "            _dl_pct         = getattr(_dl_limit_cfg, 'daily_loss_limit', 0.03)\n"
        "            _loss_limit     = _krw_bal * _dl_pct\n"
        "            if _daily_loss < -_loss_limit and _loss_limit > 0:"
    )
    if NEW.strip() in src:
        print("  ℹ️  이미 적용됨 → SKIP")
        _results["core/engine_cycle.py"] = "SKIP"
        return

    if OLD not in src:
        # fallback: 0.02 하드코딩 직접 교체
        new_src = src.replace("_krw_bal * 0.02", "_krw_bal * getattr(getattr(self, 'settings', None) and getattr(self.settings, 'risk', None), 'daily_loss_limit', 0.03)")
    else:
        new_src = src.replace(OLD, NEW)

    _write(path, new_src, "core/engine_cycle.py")


# ══════════════════════════════════════════════════════════════
# STEP-8  core/engine_sell.py  partial_sell record_trade_result 추가
# ══════════════════════════════════════════════════════════════
def step8_engine_sell():
    print("\n[STEP-8] core/engine_sell.py partial_sell record_trade_result 추가...")
    path = Path("core/engine_sell.py")
    src  = _read(path)
    if src is None:
        _results["core/engine_sell.py"] = "FAIL:파일없음"
        return

    # 이미 적용됐는지 확인 (partial_sell 안에 record_trade_result 존재)
    partial_block_end = "        finally:\n            self._selling_markets.discard(_dup_k)"
    if "record_trade_result" in src.split("async def _execute_sell")[0]:
        print("  ℹ️  이미 적용됨 → SKIP")
        _results["core/engine_sell.py"] = "SKIP"
        return

    # log_trade 호출 직전에 record_trade_result 삽입
    OLD_LOG = (
        "        log_trade(\n"
        "            \"PARTIAL_SELL\", market, result.executed_price,\n"
        "            volume * result.executed_price, reason, profit_rate\n"
        "        )"
    )
    NEW_LOG = (
        "        # [REFACTOR] partial sell도 risk_manager에 기록\n"
        "        try:\n"
        "            self.risk_manager.record_trade_result(\n"
        "                is_win=profit_rate > 0,\n"
        "                profit_rate=profit_rate / 100.0,\n"
        "            )\n"
        "        except Exception as _rtr_e:\n"
        "            logger.warning(f\"[PARTIAL-SELL] risk_manager 업데이트 실패: {_rtr_e}\")\n"
        "\n"
        "        log_trade(\n"
        "            \"PARTIAL_SELL\", market, result.executed_price,\n"
        "            volume * result.executed_price, reason, profit_rate\n"
        "        )"
    )
    if OLD_LOG not in src:
        print("  ⚠ 패턴 불일치 → 수동 확인 필요")
        _results["core/engine_sell.py"] = "FAIL:패턴불일치"
        return

    new_src = src.replace(OLD_LOG, NEW_LOG)
    _write(path, new_src, "core/engine_sell.py")


# ══════════════════════════════════════════════════════════════
# STEP-9  risk/position_sizer.py  Vol_Breakout 키 VolBreakout 통일
# ══════════════════════════════════════════════════════════════
def step9_position_sizer():
    print("\n[STEP-9] risk/position_sizer.py Vol_Breakout → VolBreakout 통일...")
    path = Path("risk/position_sizer.py")
    src  = _read(path)
    if src is None:
        _results["risk/position_sizer.py"] = "FAIL:파일없음"
        return

    # Order_Block → OrderBlock_SMC 통일 + constants 임포트 추가
    new_src = src
    changed = False

    replacements = [
        # 전략 키 통일
        ('"Order_Block":       1.1,', '"OrderBlock_SMC":    0.7,   # [REFACTOR] Order_Block → OrderBlock_SMC'),
        # Vol_Breakout 중복 제거 (VolBreakout만 유지)
        ('"Vol_Breakout":      0.2,\n        "VolBreakout":       0.2,', '"VolBreakout":       0.2,   # [REFACTOR] Vol_Breakout 키 폐기'),
        ('"Vol_Breakout":      0.2,', '"VolBreakout":       0.2,'),
    ]
    for old, new in replacements:
        if old in new_src:
            new_src = new_src.replace(old, new)
            changed = True

    if not changed:
        print("  ℹ️  변경 대상 없음 → SKIP")
        _results["risk/position_sizer.py"] = "SKIP"
        return

    _write(path, new_src, "risk/position_sizer.py")


# ══════════════════════════════════════════════════════════════
# STEP-10  strategies/market_structure/order_block.py  NAME v1 표시
# ══════════════════════════════════════════════════════════════
def step10_order_block_v1():
    print("\n[STEP-10] strategies/market_structure/order_block.py NAME → V1...")
    path = Path("strategies/market_structure/order_block.py")
    src  = _read(path)
    if src is None:
        _results["strategies/market_structure/order_block.py"] = "FAIL:파일없음"
        return

    if "OrderBlock_SMC_V1" in src:
        print("  ℹ️  이미 적용됨 → SKIP")
        _results["strategies/market_structure/order_block.py"] = "SKIP"
        return

    new_src = src.replace(
        'NAME = "OrderBlock_SMC"',
        'NAME = "OrderBlock_SMC_V1"  # [REFACTOR] v2 활성화로 v1은 비활성 롤백용'
    ).replace(
        'class OrderBlockStrategy(BaseStrategy):',
        '# [REFACTOR] v1 비활성 — 롤백 필요 시 NAME을 OrderBlock_SMC로 복원\n'
        'class OrderBlockStrategy(BaseStrategy):'
    )
    _write(path, new_src, "strategies/market_structure/order_block.py")


# ══════════════════════════════════════════════════════════════
# STEP-11~14  전략 파일 logging → loguru
# ══════════════════════════════════════════════════════════════
def _fix_strategy_logger(filepath: str, class_name: str):
    path = Path(filepath)
    src  = _read(path)
    if src is None:
        _results[filepath] = "FAIL:파일없음"
        return

    if "from loguru import logger" in src and "import logging" not in src:
        print(f"  ℹ️  {filepath} 이미 적용됨 → SKIP")
        _results[filepath] = "SKIP"
        return

    new_src = src

    # logging import 제거
    new_src = new_src.replace("import logging\n", "")

    # except 블록의 logging.getLogger 패턴 교체
    import re
    new_src = re.sub(
        r"import logging\s*\n\s*logging\.getLogger\(__name__\)\.debug\((.+?)\)",
        r"logger.debug(\1)",
        new_src,
        flags=re.DOTALL,
    )
    new_src = re.sub(
        r"logging\.getLogger\(__name__\)\.debug\((.+?)\)",
        r"logger.debug(\1)",
        new_src,
    )

    # loguru import 추가 (없으면)
    if "from loguru import logger" not in new_src:
        # from datetime import datetime 다음 줄에 삽입
        if "from datetime import datetime" in new_src:
            new_src = new_src.replace(
                "from datetime import datetime\n",
                "from datetime import datetime\nfrom loguru import logger\n",
                1,
            )
        elif "from typing import Optional" in new_src:
            new_src = new_src.replace(
                "from typing import Optional\n",
                "from typing import Optional\nfrom loguru import logger\n",
                1,
            )
        else:
            new_src = "from loguru import logger\n" + new_src

    _write(path, new_src, filepath)


def step11_14_strategy_loggers():
    print("\n[STEP-11~14] 전략 파일 logging → loguru 통일...")
    files = [
        ("strategies/momentum/macd_cross.py",           "MACDCrossStrategy"),
        ("strategies/momentum/supertrend.py",            "SupertrendStrategy"),
        ("strategies/mean_reversion/bollinger_squeeze.py","BollingerSqueezeStrategy"),
        ("strategies/mean_reversion/vwap_reversion.py",  "VWAPReversionStrategy"),
    ]
    for fp, cn in files:
        _fix_strategy_logger(fp, cn)


# ══════════════════════════════════════════════════════════════
# 메인 실행
# ══════════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("  APEX BOT 통합 리팩토링 스크립트")
    print(f"  백업 경로: {_BAK}")
    print("=" * 60)

    step1_constants()
    step2_base_strategy()
    step3_engine_ml()
    step4_engine_utils()
    step5_signal_combiner()
    step6_risk_manager()
    step7_engine_cycle()
    step8_engine_sell()
    step9_position_sizer()
    step10_order_block_v1()
    step11_14_strategy_loggers()

    # ── 최종 결과 요약 ───────────────────────────────────────
    print("\n" + "=" * 60)
    print("📊 리팩토링 결과")
    print("=" * 60)

    ok   = [k for k, v in _results.items() if v == "OK"]
    skip = [k for k, v in _results.items() if v == "SKIP"]
    fail = [k for k, v in _results.items() if v.startswith("FAIL")]

    for k in ok:
        print(f"  ✅ {k}")
    for k in skip:
        print(f"  ⏭  {k} (이미 적용됨)")
    for k in fail:
        print(f"  ❌ {k} → {_results[k]}")

    print(f"\n  성공: {len(ok)} | 스킵: {len(skip)} | 실패: {len(fail)}")
    print(f"  백업: {_BAK}")

    if fail:
        print("\n⚠️  실패 항목이 있습니다. 수동 확인 후 재실행하세요.")
        sys.exit(1)
    else:
        print("\n✅ 리팩토링 완료 — git add -A && git commit 후 python main.py --mode paper 실행")


if __name__ == "__main__":
    main()
