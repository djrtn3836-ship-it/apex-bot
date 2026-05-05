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
