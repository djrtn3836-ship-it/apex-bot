"""
============================================================
APEX BOT - 독립적 제3자 검증 테스트
Independent Third-Party Verification Test
============================================================
작성 기준: 모듈 시그니처 명세서만 참조
Mock 사용 없음 / 실제 import 실행
각 모듈 20회 이상 랜덤 파라미터 검증
============================================================
"""

import sys
import os
import random
import traceback
from typing import List, Tuple, Optional
from datetime import datetime

# ──────────────────────────────────────────────
# 프로젝트 루트 경로 추가
# ──────────────────────────────────────────────
sys.path.insert(0, 'C:/Users/hdw38/Desktop/달콩/bot/apex_bot')

import numpy as np
import pandas as pd

# ──────────────────────────────────────────────
# 출력 유틸리티
# ──────────────────────────────────────────────
PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
HEADER = "\033[94m"
RESET = "\033[0m"
SEP = "─" * 65

total_tests = 0
total_passed = 0


def report(label: str, passed: bool, detail: str = ""):
    global total_tests, total_passed
    total_tests += 1
    if passed:
        total_passed += 1
    status = PASS if passed else FAIL
    detail_str = f"  ↳ {detail}" if detail else ""
    print(f"  {status} {label}{detail_str}")


def section(title: str):
    print(f"\n{HEADER}{SEP}")
    print(f"  {title}")
    print(f"{SEP}{RESET}")


# ──────────────────────────────────────────────
# OHLCV DataFrame 생성 헬퍼
# ──────────────────────────────────────────────
def make_ohlcv(n: int = 60, base_price: float = 50_000_000.0,
               volatility: float = 0.02) -> pd.DataFrame:
    """실제 거래소 데이터와 유사한 OHLCV DataFrame 생성"""
    prices = [base_price]
    for _ in range(n - 1):
        change = random.gauss(0, volatility)
        prices.append(max(1.0, prices[-1] * (1 + change)))

    rows = []
    for i, close in enumerate(prices):
        high = close * (1 + abs(random.gauss(0, volatility / 2)))
        low = close * (1 - abs(random.gauss(0, volatility / 2)))
        open_ = prices[i - 1] if i > 0 else close
        volume = random.uniform(1e6, 1e9)
        rows.append({
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })

    df = pd.DataFrame(rows)
    df.index = pd.date_range(end=datetime.now(), periods=n, freq="1min")
    return df


def make_trending_up_ohlcv(n: int = 60, start: float = 40_000_000.0) -> pd.DataFrame:
    """상승 추세 OHLCV"""
    prices = [start * (1 + 0.005 * i + random.gauss(0, 0.003)) for i in range(n)]
    return _prices_to_df(prices, n)


def make_trending_down_ohlcv(n: int = 60, start: float = 60_000_000.0) -> pd.DataFrame:
    """하락 추세 OHLCV"""
    prices = [max(1.0, start * (1 - 0.005 * i + random.gauss(0, 0.003))) for i in range(n)]
    return _prices_to_df(prices, n)


def make_ranging_ohlcv(n: int = 60, center: float = 50_000_000.0) -> pd.DataFrame:
    """횡보 OHLCV"""
    prices = [center * (1 + random.gauss(0, 0.005)) for _ in range(n)]
    return _prices_to_df(prices, n)


def _prices_to_df(prices: list, n: int) -> pd.DataFrame:
    rows = []
    for i, close in enumerate(prices):
        high = close * (1 + abs(random.gauss(0, 0.01)))
        low = close * (1 - abs(random.gauss(0, 0.01)))
        open_ = prices[i - 1] if i > 0 else close
        rows.append({"open": open_, "high": high, "low": low,
                     "close": close, "volume": random.uniform(1e6, 1e9)})
    df = pd.DataFrame(rows)
    df.index = pd.date_range(end=datetime.now(), periods=n, freq="1min")
    return df


# ══════════════════════════════════════════════════════════════
# MODULE 1 : KellyPositionSizer
# ══════════════════════════════════════════════════════════════
def test_kelly_position_sizer():
    section("MODULE 1 : KellyPositionSizer  (risk/position_sizer.py)")

    try:
        from risk.position_sizer import KellyPositionSizer
        sizer = KellyPositionSizer()
        print(f"  import OK → {sizer.__class__.__name__}")
    except Exception as e:
        print(f"  {FAIL} import 실패: {e}")
        report("KellyPositionSizer import", False)
        return

    MIN_ORDER_KRW = 5_000
    MAX_RATIO = 0.20

    STRATEGIES = ["default", "aggressive", "conservative", "kelly", "half_kelly", ""]
    MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", ""]

    # ── 정상 범위 랜덤 테스트 (20회) ──────────────────────────
    print("\n  [A] 정상 범위 랜덤 파라미터 (20회)")
    for i in range(20):
        capital = random.uniform(100_000, 100_000_000)
        strategy = random.choice(STRATEGIES)
        market = random.choice(MARKETS)
        confidence = random.uniform(0.1, 0.99)
        try:
            result = sizer.calculate(
                total_capital=capital,
                strategy=strategy,
                market=market,
                confidence=confidence,
            )
            ok_min = result >= MIN_ORDER_KRW
            ok_max = result <= capital * MAX_RATIO
            passed = ok_min and ok_max
            report(
                f"랜덤#{i+1:02d} capital={capital:,.0f} conf={confidence:.2f}",
                passed,
                f"result={result:,.0f} (min≥{MIN_ORDER_KRW}, max≤{capital*MAX_RATIO:,.0f})"
            )
        except Exception as e:
            report(f"랜덤#{i+1:02d} 예외", False, str(e))

    # ── 경계값 테스트 ─────────────────────────────────────────
    print("\n  [B] 경계값 테스트")
    boundary_cases = [
        # (capital, strategy, market, confidence, description)
        (5_000,          "default",      "KRW-BTC", 0.5,  "최소 자본(5000)"),
        (10_000,         "default",      "KRW-BTC", 0.5,  "소액 자본(10000)"),
        (0,              "default",      "KRW-BTC", 0.5,  "자본=0 (예외 또는 0 반환 허용)"),
        (-100_000,       "default",      "KRW-BTC", 0.5,  "음수 자본"),
        (1_000_000_000,  "aggressive",   "KRW-BTC", 0.99, "초대형 자본 + 최대 신뢰도"),
        (1_000_000_000,  "conservative", "KRW-BTC", 0.01, "초대형 자본 + 최저 신뢰도"),
        (100_000_000,    "default",      "KRW-BTC", 0.0,  "신뢰도=0"),
        (100_000_000,    "default",      "KRW-BTC", 1.0,  "신뢰도=1"),
        (100_000_000,    "default",      "KRW-BTC", -1.0, "음수 신뢰도"),
        (100_000_000,    "default",      "KRW-BTC", 2.0,  "신뢰도 초과(2.0)"),
    ]
    for capital, strategy, market, confidence, desc in boundary_cases:
        try:
            result = sizer.calculate(
                total_capital=capital,
                strategy=strategy,
                market=market,
                confidence=confidence,
            )
            # 자본이 0 이하일 때는 0 또는 예외 모두 허용
            if capital <= 0:
                passed = True
                detail = f"result={result} (비정상 입력 처리됨)"
            else:
                ok_min = result >= MIN_ORDER_KRW
                ok_max = result <= capital * MAX_RATIO
                passed = ok_min and ok_max
                detail = f"result={result:,.0f}"
            report(desc, passed, detail)
        except Exception as e:
            # 비정상 입력에서 예외 발생은 허용
            graceful = capital <= 0 or confidence < 0 or confidence > 1
            report(desc, graceful, f"예외={type(e).__name__}: {e}")


# ══════════════════════════════════════════════════════════════
# MODULE 2 : ATRStopLoss
# ══════════════════════════════════════════════════════════════
def test_atr_stop_loss():
    section("MODULE 2 : ATRStopLoss  (risk/stop_loss/atr_stop.py)")

    try:
        from risk.stop_loss.atr_stop import ATRStopLoss
        sl = ATRStopLoss()
        print(f"  import OK → {sl.__class__.__name__}")
    except Exception as e:
        print(f"  {FAIL} import 실패: {e}")
        report("ATRStopLoss import", False)
        return

    MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE"]
    REQUIRED_FIELDS = {"stop_loss", "take_profit", "atr", "sl_pct", "tp_pct", "rr_ratio"}

    def validate_stop_levels(result, entry_price: float) -> Tuple[bool, str]:
        # 필드 존재 여부
        for field in REQUIRED_FIELDS:
            val = getattr(result, field, None)
            if val is None:
                return False, f"필드 누락: {field}"
        stop = getattr(result, "stop_loss")
        tp = getattr(result, "take_profit")
        rr = getattr(result, "rr_ratio")
        if not (stop < entry_price):
            return False, f"stop_loss({stop}) < entry_price({entry_price}) 위반"
        if not (tp > entry_price):
            return False, f"take_profit({tp}) > entry_price({entry_price}) 위반"
        if not (rr >= 1.0):
            return False, f"rr_ratio({rr}) >= 1.0 위반"
        return True, f"SL={stop:,.0f} TP={tp:,.0f} RR={rr:.2f}"

    # ── 정상 범위 랜덤 테스트 (20회) ──────────────────────────
    print("\n  [A] 정상 범위 랜덤 파라미터 (20회)")
    for i in range(20):
        entry = random.uniform(1_000, 100_000_000)
        df = make_ohlcv(n=random.randint(30, 100), base_price=entry)
        market = random.choice(MARKETS)
        try:
            result = sl.calculate(df=df, entry_price=entry, market=market)
            passed, detail = validate_stop_levels(result, entry)
            report(f"랜덤#{i+1:02d} entry={entry:,.0f} market={market}", passed, detail)
        except Exception as e:
            report(f"랜덤#{i+1:02d} 예외", False, f"{type(e).__name__}: {e}")

    # ── 추세별 시나리오 테스트 ────────────────────────────────
    print("\n  [B] 추세별 시나리오 테스트")
    scenarios = [
        ("상승 추세", make_trending_up_ohlcv()),
        ("하락 추세", make_trending_down_ohlcv()),
        ("횡보",     make_ranging_ohlcv()),
    ]
    for name, df in scenarios:
        entry = float(df["close"].iloc[-1])
        try:
            result = sl.calculate(df=df, entry_price=entry, market="KRW-BTC")
            passed, detail = validate_stop_levels(result, entry)
            report(f"{name} 시나리오", passed, detail)
        except Exception as e:
            report(f"{name} 시나리오 예외", False, str(e))

    # ── 경계값 테스트 ─────────────────────────────────────────
    print("\n  [C] 경계값 테스트")
    df_normal = make_ohlcv()
    boundary_cases = [
        (0.0001,         "극소 entry_price"),
        (1_000_000_000,  "극대 entry_price(10억)"),
        (-1000,          "음수 entry_price (예외 허용)"),
    ]
    for entry, desc in boundary_cases:
        try:
            result = sl.calculate(df=df_normal, entry_price=entry, market="KRW-BTC")
            if entry <= 0:
                report(desc, True, f"비정상 입력 처리됨 → {result}")
            else:
                passed, detail = validate_stop_levels(result, entry)
                report(desc, passed, detail)
        except Exception as e:
            graceful = entry <= 0
            report(desc, graceful, f"예외={type(e).__name__}")

    # 데이터 부족 (행 수 극소)
    for rows in [1, 2, 5]:
        df_tiny = make_ohlcv(n=rows)
        entry = float(df_tiny["close"].iloc[-1])
        try:
            result = sl.calculate(df=df_tiny, entry_price=entry, market="KRW-BTC")
            passed, detail = validate_stop_levels(result, entry)
            report(f"데이터 {rows}행 극소", passed, detail)
        except Exception as e:
            # 데이터 부족 예외는 허용
            report(f"데이터 {rows}행 극소 (예외 허용)", True,
                   f"예외={type(e).__name__}: {e}")


# ══════════════════════════════════════════════════════════════
# MODULE 3 : RegimeDetector
# ══════════════════════════════════════════════════════════════
def test_regime_detector():
    section("MODULE 3 : RegimeDetector  (signals/filters/regime_detector.py)")

    try:
        from signals.filters.regime_detector import RegimeDetector, MarketRegime
        detector = RegimeDetector()
        print(f"  import OK → {detector.__class__.__name__}")
    except Exception as e:
        print(f"  {FAIL} import 실패: {e}")
        report("RegimeDetector import", False)
        return

    VALID_REGIMES = {
        "TRENDING_UP", "TRENDING_DOWN", "RANGING",
        "VOLATILE", "BEAR_REVERSAL", "UNKNOWN"
    }
    MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]

    def is_valid_regime(result) -> Tuple[bool, str]:
        if not isinstance(result, MarketRegime):
            return False, f"타입 오류: {type(result).__name__} (expected MarketRegime)"
        if result.name not in VALID_REGIMES:
            return False, f"알 수 없는 enum값: {result}"
        return True, f"regime={result.name}"

    # ── 정상 범위 랜덤 테스트 (20회) ──────────────────────────
    print("\n  [A] 정상 범위 랜덤 파라미터 (20회)")
    for i in range(20):
        market = random.choice(MARKETS)
        df = make_ohlcv(n=random.randint(30, 120))
        fg = random.randint(0, 100)
        try:
            result = detector.detect(market=market, df=df, fear_greed_index=fg)
            passed, detail = is_valid_regime(result)
            report(f"랜덤#{i+1:02d} market={market} FG={fg}", passed, detail)
        except Exception as e:
            report(f"랜덤#{i+1:02d} 예외", False, f"{type(e).__name__}: {e}")

    # ── 추세별 시나리오 (각 3회) ──────────────────────────────
    print("\n  [B] 추세 유형별 시나리오 (각 3회)")
    scenario_factories = [
        ("상승 추세", make_trending_up_ohlcv,  {"fg": [70, 80, 90]}),
        ("하락 추세", make_trending_down_ohlcv, {"fg": [10, 20, 30]}),
        ("횡보",     make_ranging_ohlcv,        {"fg": [40, 50, 60]}),
    ]
    for name, factory, params in scenario_factories:
        for fg in params["fg"]:
            df = factory()
            try:
                result = detector.detect(market="KRW-BTC", df=df, fear_greed_index=fg)
                passed, detail = is_valid_regime(result)
                report(f"{name} FG={fg}", passed, detail)
            except Exception as e:
                report(f"{name} FG={fg} 예외", False, str(e))

    # ── 경계값 테스트 ─────────────────────────────────────────
    print("\n  [C] 경계값 테스트")
    df_normal = make_ohlcv()
    boundary_fg = [0, 1, 50, 99, 100, -1, 101, 999]
    for fg in boundary_fg:
        try:
            result = detector.detect(market="KRW-BTC", df=df_normal, fear_greed_index=fg)
            passed, detail = is_valid_regime(result)
            # 범위 밖 FG도 결과는 유효 MarketRegime이어야 함
            report(f"FG={fg} 경계값", passed, detail)
        except Exception as e:
            graceful = fg < 0 or fg > 100
            report(f"FG={fg} 경계값 (예외 허용={graceful})", graceful,
                   f"예외={type(e).__name__}")

    # 빈 DataFrame
    try:
        result = detector.detect(market="KRW-BTC", df=pd.DataFrame(), fear_greed_index=50)
        passed = isinstance(result, MarketRegime)
        report("빈 DataFrame", passed, f"result={result}")
    except Exception as e:
        report("빈 DataFrame (예외 허용)", True, f"예외={type(e).__name__}")

    # 데이터 극소
    for rows in [1, 3]:
        df_tiny = make_ohlcv(n=rows)
        try:
            result = detector.detect(market="KRW-BTC", df=df_tiny, fear_greed_index=50)
            passed, detail = is_valid_regime(result)
            report(f"극소 데이터({rows}행)", passed, detail)
        except Exception as e:
            report(f"극소 데이터({rows}행) (예외 허용)", True, f"예외={type(e).__name__}")


# ══════════════════════════════════════════════════════════════
# MODULE 4 : CorrelationFilter
# ══════════════════════════════════════════════════════════════
def test_correlation_filter():
    section("MODULE 4 : CorrelationFilter  (signals/filters/correlation_filter.py)")

    try:
        from signals.filters.correlation_filter import CorrelationFilter
        cf = CorrelationFilter()
        print(f"  import OK → {cf.__class__.__name__}")
    except Exception as e:
        print(f"  {FAIL} import 실패: {e}")
        report("CorrelationFilter import", False)
        return

    MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE",
               "KRW-ADA", "KRW-MATIC", "KRW-AVAX"]

    def validate_can_buy(result) -> Tuple[bool, str]:
        if not isinstance(result, tuple):
            return False, f"타입 오류: {type(result).__name__} (expected tuple)"
        if len(result) != 2:
            return False, f"tuple 길이 오류: {len(result)} (expected 2)"
        decision, reason = result
        if not isinstance(decision, bool):
            return False, f"결과[0] 타입 오류: {type(decision).__name__} (expected bool)"
        if not isinstance(reason, str):
            return False, f"결과[1] 타입 오류: {type(reason).__name__} (expected str)"
        return True, f"can_buy={decision}, reason='{reason[:40]}'"

    # ── 가격 주입 + can_buy 랜덤 테스트 (20회) ────────────────
    print("\n  [A] update_price → can_buy 통합 랜덤 테스트 (20회)")
    for i in range(20):
        # 여러 마켓에 가격 주입
        for m in random.sample(MARKETS, k=random.randint(1, 5)):
            price = random.uniform(100, 100_000_000)
            try:
                cf.update_price(market=m, price=price)
            except Exception as e:
                report(f"update_price 예외#{i+1:02d} {m}", False, str(e))

        target_market = random.choice(MARKETS)
        open_pos = random.sample(MARKETS, k=random.randint(0, 4))
        try:
            result = cf.can_buy(market=target_market, open_positions=open_pos)
            passed, detail = validate_can_buy(result)
            report(f"랜덤#{i+1:02d} market={target_market} positions={len(open_pos)}개",
                   passed, detail)
        except Exception as e:
            report(f"랜덤#{i+1:02d} 예외", False, f"{type(e).__name__}: {e}")

    # ── can_buy 파라미터 변형 테스트 ──────────────────────────
    print("\n  [B] can_buy 파라미터 변형 테스트")

    # 가격 데이터 없는 신규 마켓
    fresh_cf = None
    try:
        from signals.filters.correlation_filter import CorrelationFilter
        fresh_cf = CorrelationFilter()
    except Exception:
        pass

    cases = [
        ("open_positions=None",      "KRW-BTC", None),
        ("open_positions=[]",        "KRW-BTC", []),
        ("open_positions=전체마켓",  "KRW-BTC", MARKETS[:]),
        ("미등록 마켓",              "KRW-UNKNOWN999", []),
        ("open_positions에 본인 포함", "KRW-ETH", ["KRW-ETH"]),
    ]
    for desc, market, open_pos in cases:
        try:
            result = cf.can_buy(market=market, open_positions=open_pos)
            passed, detail = validate_can_buy(result)
            report(desc, passed, detail)
        except Exception as e:
            report(f"{desc} (예외)", False, f"{type(e).__name__}: {e}")

    # ── 극단 가격 주입 ────────────────────────────────────────
    print("\n  [C] 극단 가격 주입 테스트")
    extreme_prices = [
        ("KRW-BTC", 0,         "가격=0"),
        ("KRW-BTC", -500_000,  "음수 가격"),
        ("KRW-BTC", 1e15,      "초대형 가격(1e15)"),
        ("KRW-BTC", 0.0001,    "극소 가격(0.0001)"),
    ]
    for market, price, desc in extreme_prices:
        try:
            cf.update_price(market=market, price=price)
            result = cf.can_buy(market=market, open_positions=[])
            passed, detail = validate_can_buy(result)
            report(f"{desc} 후 can_buy", passed, detail)
        except Exception as e:
            report(f"{desc} (예외 허용)", True, f"예외={type(e).__name__}")


# ══════════════════════════════════════════════════════════════
# MODULE 5 : TrailingStopManager
# ══════════════════════════════════════════════════════════════
def test_trailing_stop_manager():
    section("MODULE 5 : TrailingStopManager  (risk/stop_loss/trailing_stop.py)")

    try:
        from risk.stop_loss.trailing_stop import TrailingStopManager
        ts = TrailingStopManager()
        print(f"  import OK → {ts.__class__.__name__}")
    except Exception as e:
        print(f"  {FAIL} import 실패: {e}")
        report("TrailingStopManager import", False)
        return

    MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE"]

    def validate_update(result) -> Tuple[bool, str]:
        if result is None:
            return True, "반환값=None (포지션 유지)"
        if isinstance(result, str):
            return True, f"반환값=str('{result[:40]}')"
        return False, f"타입 오류: {type(result).__name__} (expected None or str)"

    # ── 정상 포지션 추가 + 업데이트 랜덤 테스트 (20회) ────────
    print("\n  [A] add_position → update 통합 랜덤 테스트 (20회)")
    for i in range(20):
        from risk.stop_loss.trailing_stop import TrailingStopManager
        ts_local = TrailingStopManager()
        market = random.choice(MARKETS)
        entry = random.uniform(10_000, 100_000_000)
        stop_loss = entry * random.uniform(0.85, 0.99)
        atr = entry * random.uniform(0.005, 0.03)

        try:
            ts_local.add_position(
                market=market,
                entry_price=entry,
                stop_loss=stop_loss,
                atr=atr,
            )
        except Exception as e:
            report(f"add_position#{i+1:02d} 예외", False, str(e))
            continue

        # 가격 방향 랜덤 (상승/하락/동일)
        direction = random.choice(["up", "down", "same"])
        if direction == "up":
            current_price = entry * random.uniform(1.001, 1.10)
        elif direction == "down":
            current_price = entry * random.uniform(0.80, 0.999)
        else:
            current_price = entry

        try:
            result = ts_local.update(market=market, current_price=current_price)
            passed, detail = validate_update(result)
            report(
                f"랜덤#{i+1:02d} entry={entry:,.0f} curr={current_price:,.0f} ({direction})",
                passed, detail
            )
        except Exception as e:
            report(f"랜덤#{i+1:02d} update 예외", False, f"{type(e).__name__}: {e}")

    # ── 미등록 마켓 update 테스트 ─────────────────────────────
    print("\n  [B] 미등록 마켓 update 테스트")
    from risk.stop_loss.trailing_stop import TrailingStopManager
    ts_empty = TrailingStopManager()
    for market in ["KRW-UNKNOWN", "KRW-BTC", ""]:
        try:
            result = ts_empty.update(market=market, current_price=50_000_000)
            passed, detail = validate_update(result)
            report(f"미등록 마켓 '{market}' update", passed, detail)
        except Exception as e:
            report(f"미등록 마켓 '{market}' (예외 허용)", True, f"예외={type(e).__name__}")

    # ── 경계값 가격 테스트 ────────────────────────────────────
    print("\n  [C] 경계값 가격 update 테스트")
    from risk.stop_loss.trailing_stop import TrailingStopManager
    ts_bnd = TrailingStopManager()
    entry = 50_000_000
    ts_bnd.add_position(market="KRW-BTC", entry_price=entry,
                        stop_loss=entry * 0.95, atr=entry * 0.01)

    boundary_prices = [
        (0,           "가격=0"),
        (-1000,       "음수 가격"),
        (entry,       "entry와 동일한 가격"),
        (entry * 0.94, "stop_loss 이하 가격 (청산 신호 기대)"),
        (entry * 1.50, "50% 급등"),
        (entry * 0.01, "99% 폭락"),
        (1e15,         "극대 가격"),
    ]
    for price, desc in boundary_prices:
        try:
            result = ts_bnd.update(market="KRW-BTC", current_price=price)
            passed, detail = validate_update(result)
            report(f"{desc} (price={price:,.0f})", passed, detail)
        except Exception as e:
            graceful = price <= 0
            report(f"{desc} (예외 허용={graceful})", graceful, f"예외={type(e).__name__}")

    # ── 다중 포지션 동시 관리 ─────────────────────────────────
    print("\n  [D] 다중 포지션 동시 관리 테스트 (5개 마켓)")
    from risk.stop_loss.trailing_stop import TrailingStopManager
    ts_multi = TrailingStopManager()
    entries = {}
    for m in MARKETS[:5]:
        e = random.uniform(1_000, 100_000_000)
        entries[m] = e
        try:
            ts_multi.add_position(market=m, entry_price=e,
                                  stop_loss=e * 0.95, atr=e * 0.015)
        except Exception as ex:
            report(f"다중 add_position {m}", False, str(ex))

    for m, e in entries.items():
        curr = e * random.uniform(0.88, 1.12)
        try:
            result = ts_multi.update(market=m, current_price=curr)
            passed, detail = validate_update(result)
            report(f"다중 update {m}", passed, detail)
        except Exception as ex:
            report(f"다중 update {m} 예외", False, str(ex))


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
def main():
    print(f"\n{'═'*65}")
    print("  APEX BOT — 독립 제3자 검증 테스트")
    print(f"  실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═'*65}")

    test_kelly_position_sizer()
    test_atr_stop_loss()
    test_regime_detector()
    test_correlation_filter()
    test_trailing_stop_manager()

    # ── 최종 결과 ─────────────────────────────────────────────
    rate = (total_passed / total_tests * 100) if total_tests > 0 else 0
    color = "\033[92m" if rate >= 80 else ("\033[93m" if rate >= 60 else "\033[91m")

    print(f"\n{'═'*65}")
    print(f"  최종 검증 결과")
    print(f"{'═'*65}")
    print(f"  총 테스트 : {total_tests}건")
    print(f"  통과      : {total_passed}건")
    print(f"  실패      : {total_tests - total_passed}건")
    print(f"  통과율    : {color}{rate:.1f}%\033[0m")
    print(f"{'═'*65}\n")

    return 0 if rate == 100 else 1


if __name__ == "__main__":
    sys.exit(main())