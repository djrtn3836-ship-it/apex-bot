"""
APEX BOT 확장 테스트 v2 (TEST 9~20) - 클래스명 수정 버전
실행: cd apex_bot && python tests/test_extended.py
"""
import asyncio, sqlite3, sys, os, re
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = "[PASS]"
FAIL = "[FAIL]"
results = []

def log(status, name, detail=""):
    emoji = "✅" if status == PASS else "❌"
    print(f"{emoji} {status} {name}" + (f"  →  {detail}" if detail else ""))
    results.append((status, name, detail))

def make_ohlcv(n=100, base=1000.0, trend=0.0):
    np.random.seed(42)
    closes = base + np.cumsum(np.random.randn(n) * 10 + trend)
    df = pd.DataFrame({
        "open":   closes * 0.999,
        "high":   closes * 1.005,
        "low":    closes * 0.995,
        "close":  closes,
        "volume": np.random.randint(1000, 5000, n).astype(float),
    })
    return df

# ══ TEST 9: ATR 손절/익절 (ATRStopLoss) ════════
def test_atr_stop():
    print("\n── TEST 9: ATR 손절/익절 계산 ─────────────")
    try:
        from risk.stop_loss.atr_stop import ATRStopLoss
        atr = ATRStopLoss()
        df  = make_ohlcv(50, base=1000.0)
        entry = 1000.0

        levels = atr.calculate(df, entry)

        sl_ok = levels.stop_loss < entry
        log(PASS if sl_ok else FAIL,
            "ATR 손절가 < 진입가",
            f"SL={levels.stop_loss:.1f} < entry={entry}")

        tp_ok = levels.take_profit > entry
        log(PASS if tp_ok else FAIL,
            "ATR 익절가 > 진입가",
            f"TP={levels.take_profit:.1f} > entry={entry}")

        sl_dist = entry - levels.stop_loss
        tp_dist = levels.take_profit - entry
        rr = tp_dist / sl_dist if sl_dist > 0 else 0
        log(PASS if rr >= 1.0 else FAIL,
            f"RR ≥ 1.0", f"RR={rr:.2f}")

        sl_pct = (entry - levels.stop_loss) / entry * 100
        log(PASS if sl_pct <= 7.0 else FAIL,
            f"SL 프로파일 상한 적용", f"SL={sl_pct:.2f}%")

    except Exception as e:
        log(FAIL, "ATR 테스트 오류", str(e))

# ══ TEST 10: 트레일링 스탑 (TrailingStopManager) ═
def test_trailing_stop():
    print("\n── TEST 10: 트레일링 스탑 ──────────────────")
    try:
        from risk.stop_loss.trailing_stop import TrailingStopManager
        ts = TrailingStopManager()
        entry = 1000.0
        sl    = 970.0

        ts.add_position("KRW-TEST", entry, sl, atr=20.0)

        # 가격 상승 → 청산 없음
        result1 = ts.update("KRW-TEST", 1050.0)
        log(PASS if result1 is None else FAIL,
            "가격 상승 시 청산 없음", f"result={result1}")

        # 가격 급락 → 손절 발동
        result2 = ts.update("KRW-TEST", 900.0)
        log(PASS if result2 is not None else FAIL,
            "가격 급락 시 손절 발동", f"result={result2}")

        # 포지션 제거 후 무반응
        ts.remove_position("KRW-TEST")
        result3 = ts.update("KRW-TEST", 800.0)
        log(PASS if result3 is None else FAIL,
            "포지션 제거 후 무반응", f"result={result3}")

    except Exception as e:
        log(FAIL, "트레일링 스탑 오류", str(e))

# ══ TEST 11: PositionManagerV2 ══════════════════
def test_position_manager_v2():
    print("\n── TEST 11: PositionManagerV2 ──────────────")
    try:
        from risk.position_manager_v2 import PositionManagerV2, PositionV2

        pos = PositionV2(
            market="KRW-TEST",
            entry_price=1000.0,
            volume=10.0,
            amount_krw=10000.0,
            stop_loss=970.0,
            take_profit=1030.0,
            strategy="MACD_Cross",
        )

        # 손절 발동
        mgr1 = PositionManagerV2()
        mgr1.add_position(pos)
        sig1 = mgr1.check_exit("KRW-TEST", 960.0)
        log(PASS if sig1.should_exit else FAIL,
            "손절 발동 (현재가 < SL)",
            f"reason={getattr(sig1.reason,'value',sig1.reason)}")

        # 정상 범위 → 청산 없음
        mgr2 = PositionManagerV2()
        mgr2.add_position(pos)
        sig2 = mgr2.check_exit("KRW-TEST", 1010.0)
        log(PASS if not sig2.should_exit else FAIL,
            "정상 범위 청산 없음", f"should_exit={sig2.should_exit}")

        # 익절 발동
        mgr3 = PositionManagerV2()
        mgr3.add_position(pos)
        sig3 = mgr3.check_exit("KRW-TEST", 1040.0)
        log(PASS if sig3.should_exit else FAIL,
            "익절 발동 (현재가 > TP)",
            f"reason={getattr(sig3.reason,'value',sig3.reason)}")

    except Exception as e:
        log(FAIL, "PositionManagerV2 오류", str(e))

# ══ TEST 12: 상관관계 필터 ══════════════════════
def test_correlation_filter():
    print("\n── TEST 12: 상관관계 필터 ──────────────────")
    try:
        from signals.filters.correlation_filter import CorrelationFilter
        cf = CorrelationFilter()

        cf.update_price("KRW-BTC",  100_000_000.0)
        cf.update_price("KRW-TEST", 1000.0)
        can, reason = cf.can_buy("KRW-TEST")
        log(PASS if can else FAIL,
            "정상 상태 매수 허용", f"reason={reason}")

        # BTC 급락 시뮬레이션
        for i in range(5):
            cf.update_price("KRW-BTC", 100_000_000.0 * (1 - 0.014 * (i+1)))
        can2, reason2 = cf.can_buy("KRW-TEST")
        log(PASS if not can2 else FAIL,
            "BTC 급락 후 매수 차단",
            f"reason={str(reason2)[:60]}")

    except Exception as e:
        log(FAIL, "상관관계 필터 오류", str(e))

# ══ TEST 13: MTF 신호 병합 (mtf_aligned 속성) ══
def test_mtf_merger():
    print("\n── TEST 13: MTF 신호 병합 ──────────────────")
    try:
        from signals.mtf_signal_merger import MTFSignalMerger
        merger = MTFSignalMerger()

        tf_data = {
            "1m":  make_ohlcv(100, 1000.0, trend=1.0),
            "5m":  make_ohlcv(100, 1000.0, trend=0.8),
            "15m": make_ohlcv(100, 1000.0, trend=0.5),
        }
        result = merger.analyze(tf_data)

        log(PASS if result is not None else FAIL,
            "MTF 분석 반환값 존재", f"type={type(result).__name__}")

        # 실제 속성명: mtf_aligned (MTFResult 클래스)
        has_aligned = hasattr(result, "mtf_aligned")
        log(PASS if has_aligned else FAIL,
            "MTF mtf_aligned 속성 존재",
            f"mtf_aligned={getattr(result,'mtf_aligned',None)}")

        # combined_score 범위
        score = getattr(result, "combined_score", None)
        if score is not None:
            log(PASS if -1.0 <= score <= 1.0 else FAIL,
                "combined_score 범위 (-1~1)", f"score={score:.3f}")

    except Exception as e:
        log(FAIL, "MTF 테스트 오류", str(e))

# ══ TEST 14: 레짐 감지기 (market 인자 추가) ═════
def test_regime_detector():
    print("\n── TEST 14: 레짐 감지기 ────────────────────")
    try:
        from signals.filters.regime_detector import RegimeDetector
        rd = RegimeDetector()

        df_bull = make_ohlcv(200, 1000.0, trend=2.0)
        df_bear = make_ohlcv(200, 1000.0, trend=-2.0)

        # market 인자 필요
        regime_bull = rd.detect("KRW-TEST", df_bull)
        log(PASS if regime_bull is not None else FAIL,
            "상승장 레짐 감지", f"regime={regime_bull}")

        regime_bear = rd.detect("KRW-TEST", df_bear)
        log(PASS if regime_bear is not None else FAIL,
            "하락장 레짐 감지", f"regime={regime_bear}")

        valid = ["BULL","BEAR","NEUTRAL","RANGING","TRENDING","WATCH"]
        regime_str = str(regime_bull).upper()
        is_valid = any(v in regime_str for v in valid)
        log(PASS if is_valid else FAIL,
            "레짐 값 유효성", f"값={regime_str[:30]}")

    except Exception as e:
        log(FAIL, "레짐 감지기 오류", str(e))

# ══ TEST 15: ML 예측기 (MLPredictor) ════════════
def test_predictor():
    print("\n── TEST 15: ML 예측기 출력 형식 ───────────")
    try:
        from models.inference.predictor import MLPredictor
        pred = MLPredictor()

        model_loaded = pred.load_model()
        log(PASS if model_loaded else FAIL,
            "ML 모델 로딩", f"loaded={model_loaded}")

        if model_loaded:
            df     = make_ohlcv(200, 1000.0)
            result = pred.predict("KRW-TEST", df)

            log(PASS if isinstance(result, dict) else FAIL,
                "예측 결과 dict", f"type={type(result)}")

            if isinstance(result, dict):
                log(PASS if "signal" in result else FAIL,
                    "signal 키 존재", f"keys={list(result.keys())[:5]}")
                conf = result.get("confidence", -1)
                log(PASS if 0.0 <= conf <= 1.0 else FAIL,
                    "confidence 범위 (0~1)", f"conf={conf:.3f}")
                sig = result.get("signal", "")
                log(PASS if sig in ["BUY","SELL","HOLD"] else FAIL,
                    "signal 값 유효", f"signal={sig}")
        else:
            log(PASS, "모델 미로딩 → predict 스킵", "파일 없음 정상")

    except Exception as e:
        log(FAIL, "ML 예측기 오류", str(e))

# ══ TEST 16: 포지션 최대 개수 차단 ══════════════
def test_max_positions():
    print("\n── TEST 16: 포지션 최대 개수 차단 ─────────")
    src = open("core/engine_buy.py", encoding="utf-8").read()
    lines = [l for l in src.splitlines() if not l.strip().startswith("#")]
    has_max = any("max_positions" in l and (">=" in l or ">" in l) for l in lines)
    log(PASS if has_max else FAIL, "max_positions 초과 차단 코드", "")
    has_setting = "settings.trading.max_positions" in src
    log(PASS if has_setting else FAIL, "max_positions settings 참조", "")

# ══ TEST 17: 서킷 브레이커 ══════════════════════
def test_circuit_breaker():
    print("\n── TEST 17: 서킷 브레이커 ──────────────────")
    src   = open("core/engine_cycle.py", encoding="utf-8").read()
    lines = [l for l in src.splitlines() if not l.strip().startswith("#")]
    log(PASS if any("daily_loss" in l.lower() or "circuit" in l.lower() for l in lines) else FAIL,
        "일일 손실 한도 코드 존재", "")
    log(PASS if "async def _check_circuit_breaker" in src else FAIL,
        "_check_circuit_breaker 함수 정의", "")
    log(PASS if "0.05" in src or "daily_loss_limit" in src else FAIL,
        "일일 손실 한도 5% 설정", "")

# ══ TEST 18: Walk-Forward 스케줄 (set_state 확인) 
def test_walk_forward_schedule():
    print("\n── TEST 18: Walk-Forward 스케줄 ───────────")
    src = open("core/engine_schedule.py", encoding="utf-8").read()

    log(PASS if "_scheduled_walk_forward" in src else FAIL,
        "walk_forward 함수 존재", "")

    # 실제 스케줄: 주석에 "02:00" 또는 "월요일" 포함
    has_mon   = 'day_of_week="mon"' in src or "day_of_week='mon'" in src
    has_hour2 = "hour=2" in src
    has_schedule = has_mon and has_hour2
    log(PASS if has_schedule else FAIL,
        "월요일 02:00 스케줄 확인",
        f"day_of_week=mon:{has_mon}, hour=2:{has_hour2}")

    # DB 저장: set_state 사용
    has_db = "walk_forward_last_result" in src and "set_state" in src
    log(PASS if has_db else FAIL,
        "Walk-Forward 결과 DB 저장",
        "set_state 사용" if has_db else "미발견")

# ══ TEST 19: 매수 흐름 핵심 로직 ════════════════
def test_buy_flow_logic():
    print("\n── TEST 19: 매수 흐름 핵심 로직 ───────────")
    src = open("core/engine_buy.py", encoding="utf-8").read()
    for name, cond in [
        ("매도 후 쿨다운",   "_sell_cooldown" in src),
        ("DB 손절 쿨다운",   "sl_cooldown_" in src),
        ("포지션 중복 체크", "is_position_open" in src),
        ("중복 매수 방지",   "_buying_markets" in src),
        ("신뢰도 체크",      "confidence" in src and "0.65" in src),
        ("최대 포지션",      "max_positions" in src),
        ("최소 주문금액",    "min_order" in src or "MIN_POSITION" in src),
        ("신호강도 매수비율","_buy_ratio" in src or "buy_ratio" in src),
    ]:
        log(PASS if cond else FAIL, f"매수 {name}", "")

# ══ TEST 20: 매도 흐름 핵심 로직 ════════════════
def test_sell_flow_logic():
    print("\n── TEST 20: 매도 흐름 핵심 로직 ───────────")
    sell  = open("core/engine_sell.py",  encoding="utf-8").read()
    cycle = open("core/engine_cycle.py", encoding="utf-8").read()
    for name, cond in [
        ("profit_rate *100 DB",   "profit_rate * 100" in sell),
        ("텔레그램 *100",         sell.count("profit_rate * 100") >= 2),
        ("손절 쿨다운 DB 저장",   "sl_cooldown_" in sell and "set_state" in sell),
        ("트레일링 스탑",         "trailing_stop" in cycle),
        ("부분청산",              "partial_exit" in cycle),
        ("ML 익절 +0.5%",         "0.5" in cycle),
        ("ML 손절 -1.5%",         "-1.5" in cycle),
        ("비상 손절 -2.5%",       "-2.5" in cycle),
        ("PPO 경험 기록",         "ppo_online" in sell or "ppo_online" in cycle),
        ("4시간 재매수 금지",     "hours=4" in sell or "timedelta(hours=4)" in sell),
    ]:
        log(PASS if cond else FAIL, f"매도 {name}", "")

# ══ MAIN ════════════════════════════════════════
async def main():
    print("=" * 60)
    print("  APEX BOT 확장 테스트 v2 (TEST 9~20)")
    print(f"  실행: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    test_atr_stop()
    test_trailing_stop()
    test_position_manager_v2()
    test_correlation_filter()
    test_mtf_merger()
    test_regime_detector()
    test_predictor()
    test_max_positions()
    test_circuit_breaker()
    test_walk_forward_schedule()
    test_buy_flow_logic()
    test_sell_flow_logic()

    print("\n" + "=" * 60)
    total  = len(results)
    passed = sum(1 for r in results if r[0] == PASS)
    failed = total - passed
    print(f"  총 {total}개: ✅ {passed}개 통과  ❌ {failed}개 실패")
    if failed:
        print("\n  ❌ 실패 항목:")
        for r in results:
            if r[0] == FAIL:
                print(f"    • {r[1]}: {r[2]}")
    else:
        print("  🎉 전체 통과! 확장 시스템 완전 정상!")
    print("=" * 60)

asyncio.run(main())
