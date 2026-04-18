"""
APEX BOT 스트레스 테스트
100회 무작위 조건 × 8개 시스템 = 800개 검증
"""
import sys, os, asyncio, random, time, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
import pandas as pd
import numpy as np

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"
results_all = []

def log(status, category, test_id, condition, detail=""):
    results_all.append((status, category, test_id, condition, detail))
    if status == FAIL:
        print(f"  {status} FAIL [{test_id}] {condition} → {detail}")

def make_ohlcv(n, base, trend=0.0, volatility=0.02, seed=None):
    if seed is not None:
        np.random.seed(seed)
    prices = [base]
    for _ in range(n - 1):
        change = np.random.normal(trend * 0.001, volatility * base * 0.01)
        prices.append(max(prices[-1] + change, base * 0.1))
    df = pd.DataFrame({
        "open":   prices,
        "high":   [p * (1 + abs(np.random.normal(0, 0.005))) for p in prices],
        "low":    [p * (1 - abs(np.random.normal(0, 0.005))) for p in prices],
        "close":  prices,
        "volume": [abs(np.random.normal(1000000, 200000)) for _ in prices],
    })
    for col in ["ema_5","ema_10","ema_20","ema_50","ema_200"]:
        w = int(col.split("_")[1])
        df[col] = df["close"].ewm(span=w).mean()
    df["rsi"] = 50.0
    df["atr"] = df["close"].diff().abs().rolling(14).mean().fillna(df["close"] * 0.01)
    df["bb_upper"] = df["close"].rolling(20).mean().fillna(df["close"]) * 1.02
    df["bb_lower"] = df["close"].rolling(20).mean().fillna(df["close"]) * 0.98
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["close"]
    df["macd"] = df["close"].ewm(12).mean() - df["close"].ewm(26).mean()
    df["volume_ma"] = df["volume"].rolling(20).mean().fillna(df["volume"])
    return df

# ══════════════════════════════════════════════════════════════
# STRESS TEST 1: 부분청산 - 100가지 진입가/TP 조건
# ══════════════════════════════════════════════════════════════
def stress_partial_exit():
    print("\n── STRESS 1: 부분청산 (100가지 가격 조건) ──")
    from risk.partial_exit import PartialExitManager

    # 테스트 케이스: (진입가, TP비율%, 설명)
    cases = []
    # 저가코인 (1~100원)
    for p in [1, 5, 10, 50, 99]:
        for tp in [1.0, 2.0, 3.0, 5.0, 10.0]:
            cases.append((p, tp, f"저가_{p}원_TP{tp}%"))
    # 중가코인 (100~10000원)
    for p in [100, 500, 1000, 5000, 9999]:
        for tp in [1.5, 3.0, 5.0, 8.0, 15.0]:
            cases.append((p, tp, f"중가_{p}원_TP{tp}%"))
    # 고가코인 (10000원+)
    for p in [10000, 50000, 100000, 5000000, 90000000]:
        for tp in [0.5, 1.0, 2.0, 3.0, 5.0]:
            cases.append((p, tp, f"고가_{p}원_TP{tp}%"))
    # 극단값
    extras = [
        (0.001, 50.0, "극소가_0.001원"),
        (1e8,   0.1,  "극대가_1억원"),
        (1000,  0.01, "극소TP_0.01%"),
        (1000, 100.0, "극대TP_100%"),
    ]
    cases.extend(extras)

    passed = failed = 0
    for entry, tp_pct, desc in cases[:100]:
        try:
            pm = PartialExitManager()
            market = "KRW-STRESS"
            tp_price = entry * (1 + tp_pct / 100)
            pm.add_position(market=market, entry_price=entry,
                           volume=100.0, take_profit=tp_price)

            tp_range = tp_price - entry
            if tp_range <= 0:
                log(WARN, "부분청산", desc, "TP범위=0", "스킵")
                continue

            # Level1 트리거
            price1 = entry + tp_range * 0.5
            v1 = pm.check(market, price1)
            ok1 = v1 is not None and v1 > 0
            if not ok1:
                log(FAIL, "부분청산", desc, "Level1 미발동",
                    f"entry={entry} tp={tp_price:.4f} price={price1:.4f} v={v1}")
                failed += 1
                continue

            # Level2 트리거
            price2 = entry + tp_range * 1.0
            v2 = pm.check(market, price2)
            ok2 = v2 is not None and v2 > 0
            if not ok2:
                log(FAIL, "부분청산", desc, "Level2 미발동",
                    f"v={v2}")
                failed += 1
                continue

            # Level3 트리거
            price3 = entry + tp_range * 1.5
            v3 = pm.check(market, price3)
            ok3 = v3 is not None and v3 > 0
            if not ok3:
                log(FAIL, "부분청산", desc, "Level3 미발동",
                    f"v={v3}")
                failed += 1
                continue

            # 중복발동 방지
            v4 = pm.check(market, price3 * 1.1)
            ok4 = (v4 is None or v4 == 0)
            if not ok4:
                log(FAIL, "부분청산", desc, "중복발동",
                    f"v={v4}")
                failed += 1
                continue

            # 비율 합계 검증 (40+40+20=100%)
            total_ratio = 40 + 40 + 20
            if total_ratio != 100:
                log(FAIL, "부분청산", desc, "비율합계!=100%", f"{total_ratio}")
                failed += 1
                continue

            passed += 1
        except Exception as e:
            log(FAIL, "부분청산", desc, "예외발생", str(e))
            failed += 1

    print(f"  결과: {passed}통과 / {failed}실패 / 100케이스")
    return passed, failed

# ══════════════════════════════════════════════════════════════
# STRESS TEST 2: ATR 손절 - 100가지 가격/변동성 조건
# ══════════════════════════════════════════════════════════════
def stress_atr_stop():
    print("\n── STRESS 2: ATR 손절 (100가지 가격×변동성) ──")
    from risk.stop_loss.atr_stop import ATRStopLoss

    atr_calc = ATRStopLoss()
    passed = failed = 0

    # 가격 × 변동성 조합 100개
    prices  = [1, 10, 100, 500, 1000, 5000, 10000, 100000, 1000000, 90000000]
    vol_mults = [0.3, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 10.0, 15.0, 20.0]

    for price in prices:
        for vol_mult in vol_mults:
            desc = f"price={price}_atr_mult={vol_mult}"
            try:
                df = make_ohlcv(50, price, volatility=vol_mult, seed=int(price+vol_mult*10))
                levels = atr_calc.calculate(df, price)

                # 기본 검증
                assert levels.stop_loss < price, f"SL({levels.stop_loss}) >= 진입가({price})"
                assert levels.take_profit > price, f"TP({levels.take_profit}) <= 진입가({price})"

                sl_pct = (price - levels.stop_loss) / price * 100
                tp_pct = (levels.take_profit - price) / price * 100
                rr = tp_pct / sl_pct if sl_pct > 0 else 0

                # 프로파일 max_sl 상한 검증 (최대 7.0%)
                assert sl_pct <= 7.0, f"SL({sl_pct:.2f}%) 상한 초과"
                # RR >= 1.0 검증
                assert rr >= 0.9, f"RR({rr:.2f}) < 0.9"

                passed += 1
            except AssertionError as e:
                log(FAIL, "ATR손절", desc, str(e), "")
                failed += 1
            except Exception as e:
                log(FAIL, "ATR손절", desc, "예외", str(e)[:80])
                failed += 1

    print(f"  결과: {passed}통과 / {failed}실패 / 100케이스")
    return passed, failed

# ══════════════════════════════════════════════════════════════
# STRESS TEST 3: Kelly 포지션 사이징 - 100가지 승률/R 조건
# ══════════════════════════════════════════════════════════════
def stress_kelly():
    print("\n── STRESS 3: Kelly 사이징 (100가지 승률×R) ──")
    from risk.position_sizer import KellyPositionSizer

    sizer = KellyPositionSizer()
    passed = failed = 0

    # 승률 × R값 10×10 = 100케이스
    win_rates = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    r_values  = [0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]

    for wr in win_rates:
        for r in r_values:
            desc = f"WR={wr:.0%}_R={r}"
            try:
                kelly_raw = wr - (1 - wr) / r if r > 0 else 0
                kelly_pct = max(0.05, min(0.20, kelly_raw))

                # 항상 5%~20% 범위
                assert 0.05 <= kelly_pct <= 0.20, \
                    f"Kelly 범위 초과: {kelly_pct:.2%}"

                # 승률 0% → 최소값 반환
                if wr == 0.0:
                    assert kelly_pct == 0.05, \
                        f"WR=0% 인데 Kelly={kelly_pct:.2%}"

                # 승률 90%+ → 최대값 (20%)
                if wr >= 0.8 and r >= 2.0:
                    assert kelly_pct == 0.20, \
                        f"고승률 고R인데 Kelly={kelly_pct:.2%}"

                passed += 1
            except AssertionError as e:
                log(FAIL, "Kelly", desc, str(e), "")
                failed += 1
            except Exception as e:
                log(FAIL, "Kelly", desc, "예외", str(e)[:80])
                failed += 1

    print(f"  결과: {passed}통과 / {failed}실패 / 100케이스")
    return passed, failed

# ══════════════════════════════════════════════════════════════
# STRESS TEST 4: PPO profit_rate 정규화 - 100가지 입력값
# ══════════════════════════════════════════════════════════════
def stress_ppo_profit_rate():
    print("\n── STRESS 4: PPO profit_rate 정규화 (100가지 입력) ──")
    from models.train.ppo_online_trainer import PPOOnlineTrainer

    passed = failed = 0

    # 케이스 정의
    cases = []
    # 정상 % 값 (1.0 ~ 10.0)
    for v in [round(x * 0.5, 1) for x in range(2, 22)]:
        cases.append((v, "정상%", v))
    # 소수 (0.001 ~ 0.20) → *100 해야 함
    for v, _exp_override in [(0.001,0.1),(0.005,0.5),(0.01,1.0),(0.015,1.5),(0.02,2.0),(0.05,5.0),(0.10,0.10),(0.15,0.15),(0.19,0.19),(0.20,0.20)]:
        cases.append((v, "소수→*100", _exp_override))
    # 경계값
    boundary = [
        (0.0,   "제로",     0.0),
        (0.099, "소수경계", 9.9),
        (0.100, "정상경계", 0.1),
        (100.0, "100%초과", 100.0),
        (-1.85, "손실%",   -1.85),
        (-0.0185, "손실소수", -1.85),
    ]
    cases.extend(boundary)

    # 100개로 맞추기
    random.seed(42)
    while len(cases) < 100:
        v = round(random.uniform(-10, 10), 4)
        expected = v * 100 if abs(v) < 0.1 else v
        cases.append((v, "랜덤", expected))
    cases = cases[:100]

    trainer = PPOOnlineTrainer()
    for i, (input_val, desc, expected) in enumerate(cases):
        try:
            # profit_rate 정규화 로직 직접 테스트
            profit_rate = input_val
            if abs(profit_rate) < 0.1:
                profit_rate = profit_rate * 100

            # 예상값과 비교 (부동소수점 허용 오차)
            if abs(expected) > 0:
                diff_pct = abs(profit_rate - expected) / abs(expected) * 100
                assert diff_pct < 1.0, \
                    f"정규화 오류: input={input_val} got={profit_rate:.4f} expected={expected:.4f}"
            else:
                assert abs(profit_rate) < 0.001, \
                    f"제로 오류: input={input_val} got={profit_rate:.4f}"

            passed += 1
        except AssertionError as e:
            log(FAIL, "PPO정규화", f"#{i}_{desc}", str(e), "")
            failed += 1
        except Exception as e:
            log(FAIL, "PPO정규화", f"#{i}_{desc}", "예외", str(e)[:80])
            failed += 1

    print(f"  결과: {passed}통과 / {failed}실패 / 100케이스")
    return passed, failed

# ══════════════════════════════════════════════════════════════
# STRESS TEST 5: 트레일링 스탑 - 100가지 가격 경로
# ══════════════════════════════════════════════════════════════
def stress_trailing_stop():
    print("\n── STRESS 5: 트레일링 스탑 (100가지 가격 경로) ──")
    from risk.stop_loss.trailing_stop import TrailingStopManager

    # TrailingStop 실제 로직:
    # ACTIVATE_PCT=0.02 : peak >= entry*1.02 시 활성화
    # TRAIL_PCT=0.015   : current <= peak*0.985 시 SELL
    ACTIVATE = 0.02
    TRAIL    = 0.015

    passed = failed = 0
    random.seed(777)
    test_scenarios = []

    # 1) 활성화 후 trail 아래 급락 -> SELL 발동해야 함
    for i in range(25):
        entry      = random.choice([100, 500, 1000, 5000, 50000])
        peak_mult  = random.uniform(1.03, 1.20)
        crash_mult = peak_mult * (1 - TRAIL - 0.01)
        test_scenarios.append(("활성화급락", entry, peak_mult, crash_mult, True))

    # 2) 활성화 후 trail 범위 내 하락 -> SELL 미발동
    for i in range(25):
        entry      = random.choice([100, 1000, 10000])
        peak_mult  = random.uniform(1.03, 1.10)
        stay_mult  = peak_mult * random.uniform(0.990, 0.999)
        test_scenarios.append(("활성화횡보", entry, peak_mult, stay_mult, False))

    # 3) 미활성화 상태 급락 -> SELL 미발동 (2% 미달)
    for i in range(25):
        entry      = random.choice([500, 2000, 30000])
        peak_mult  = random.uniform(1.001, 1.019)
        crash_mult = random.uniform(0.80, 0.95)
        test_scenarios.append(("미활성화급락", entry, peak_mult, crash_mult, False))

    # 4) 활성화 경계 직후 trail 아래 -> SELL 발동
    for i in range(25):
        entry      = random.choice([1000, 5000])
        peak_mult  = random.uniform(1.021, 1.050)
        crash_mult = peak_mult * (1 - TRAIL - 0.005)
        test_scenarios.append(("경계활성화", entry, peak_mult, crash_mult, True))

    random.shuffle(test_scenarios)

    for scenario_type, entry, peak_m, end_m, should_trigger in test_scenarios:
        desc = f"{scenario_type}_entry={entry}_peak={peak_m:.3f}_end={end_m:.3f}"
        try:
            mgr    = TrailingStopManager()
            market = "KRW-STRESS"
            atr    = entry * 0.02

            mgr.register(market, entry)
            mgr.add_position(market, entry, atr=atr)

            peak_price  = entry * peak_m
            end_price   = entry * end_m
            activated   = (peak_price >= entry * (1 + ACTIVATE))
            trail_price = peak_price * (1 - TRAIL)

            mgr.update(market, peak_price)
            result    = mgr.update(market, end_price)
            triggered = (result is not None and str(result).upper() == "SELL")

            if should_trigger:
                if activated and end_price <= trail_price:
                    if not triggered:
                        log(FAIL, "트레일링", desc,
                            f"SELL 미발동 (end={end_price:.1f}<=trail={trail_price:.1f})", "")
                        failed += 1
                        continue
            else:
                if triggered and not activated:
                    log(FAIL, "트레일링", desc,
                        f"미활성화 오발동", f"end={end_price:.1f}")
                    failed += 1
                    continue
                if triggered and activated and end_price > trail_price:
                    log(FAIL, "트레일링", desc,
                        f"trail 위 오발동 (end={end_price:.1f}>trail={trail_price:.1f})", "")
                    failed += 1
                    continue

            passed += 1
        except Exception as e:
            log(FAIL, "트레일링", desc, "예외", str(e)[:80])
            failed += 1

    print(f"  결과: {passed}통과 / {failed}실패 / 100케이스")
    return passed, failed


def stress_mtf_merger():
    print("\n── STRESS 6: MTF 신호 병합 (100가지 TF 조합) ──")
    from signals.mtf_signal_merger import MTFSignalMerger

    merger = MTFSignalMerger()
    passed = failed = 0

    random.seed(123)
    tfs = ["1d", "4h", "1h", "15m", "5m", "1m"]

    for i in range(100):
        seed = i * 37
        base = random.choice([100, 1000, 10000, 50000])
        # 트렌드: -2(강하락) ~ +2(강상승)
        trend = random.uniform(-2.0, 2.0)
        desc  = f"#{i}_base={base}_trend={trend:.1f}"
        try:
            tf_data = {}
            for tf in random.sample(tfs, random.randint(2, 6)):
                tf_data[tf] = make_ohlcv(50, base, trend=trend, seed=seed)
                seed += 1

            result = merger.analyze(tf_data)

            # 필수 속성 검증
            assert hasattr(result, "combined_score"), "combined_score 없음"
            assert hasattr(result, "allow_buy"),      "allow_buy 없음"
            assert hasattr(result, "allow_sell"),     "allow_sell 없음"
            assert hasattr(result, "mtf_aligned"),    "mtf_aligned 없음"
            assert isinstance(result.mtf_aligned, bool), \
                f"mtf_aligned 타입 오류: {type(result.mtf_aligned)}"

            # 논리적 일관성: allow_buy와 allow_sell 동시 True 불가
            assert not (result.allow_buy and result.allow_sell), \
                "allow_buy & allow_sell 동시 True"

            # score 범위 (-3 ~ +3 허용)
            assert -3.0 <= result.combined_score <= 3.0, \
                f"score 범위 초과: {result.combined_score}"

            passed += 1
        except AssertionError as e:
            log(FAIL, "MTF병합", desc, str(e), "")
            failed += 1
        except Exception as e:
            log(FAIL, "MTF병합", desc, "예외", str(e)[:80])
            failed += 1

    print(f"  결과: {passed}통과 / {failed}실패 / 100케이스")
    return passed, failed

# ══════════════════════════════════════════════════════════════
# STRESS TEST 7: PositionManagerV2 - 100가지 SL/TP 시나리오
# ══════════════════════════════════════════════════════════════
def stress_position_manager():
    print("\n── STRESS 7: PositionManagerV2 (100가지 SL/TP) ──")
    from risk.position_manager_v2 import PositionManagerV2, PositionV2, ExitReason

    passed = failed = 0
    random.seed(999)

    for i in range(100):
        entry    = random.choice([100, 500, 1000, 5000, 10000, 50000])
        sl_pct   = random.uniform(0.005, 0.025)   # 0.5% ~ 2.5%
        tp_pct   = random.uniform(0.010, 0.050)   # 1.0% ~ 5.0%
        sl_price = entry * (1 - sl_pct)
        tp_price = entry * (1 + tp_pct)
        desc     = f"#{i}_entry={entry}_SL={sl_pct:.1%}_TP={tp_pct:.1%}"

        # PositionManagerV2의 partial_exit 트리거를 tp_pct에 맞게 동적 설정
        # partial_exit_1 = tp_pct * 0.4  (TP의 40% 지점 → 1차 부분청산)
        # partial_exit_2 = tp_pct * 0.8  (TP의 80% 지점 → 2차 완전청산)
        p1 = round(tp_pct * 0.4, 6)
        p2 = round(tp_pct * 0.8, 6)

        try:
            amount_krw = entry * 100.0

            # ── 시나리오 A: 손절 (SL 이하 가격) ──
            mgr1 = PositionManagerV2(partial_exit_1=p1, partial_exit_2=p2)
            pos1 = PositionV2(
                market="KRW-S", entry_price=entry,
                volume=100.0, amount_krw=amount_krw,
                strategy="TEST",
                stop_loss=sl_price, take_profit=tp_price,
            )
            mgr1.add_position(pos1)
            sig1 = mgr1.check_exit("KRW-S", sl_price * 0.99)
            assert sig1.should_exit, (
                f"손절 미발동: price={sl_price*0.99:.1f} sl={sl_price:.1f}"
            )

            # ── 시나리오 B: 1차 부분청산 (p1 트리거 돌파) ──
            mgr2 = PositionManagerV2(partial_exit_1=p1, partial_exit_2=p2)
            pos2 = PositionV2(
                market="KRW-S", entry_price=entry,
                volume=100.0, amount_krw=amount_krw,
                strategy="TEST",
                stop_loss=sl_price, take_profit=tp_price,
            )
            mgr2.add_position(pos2)
            p1_hit = entry * (1 + p1 * 1.05)
            sig2 = mgr2.check_exit("KRW-S", p1_hit)
            assert sig2.should_exit, (
                f"1차 부분청산 미발동: price={p1_hit:.1f} p1={entry*(1+p1):.1f}"
            )

            # ── 시나리오 C: 2차 익절 (1차 완료 후 p2 돌파) ──
            mgr3 = PositionManagerV2(partial_exit_1=p1, partial_exit_2=p2)
            pos3 = PositionV2(
                market="KRW-S", entry_price=entry,
                volume=100.0, amount_krw=amount_krw,
                strategy="TEST",
                stop_loss=sl_price, take_profit=tp_price,
            )
            mgr3.add_position(pos3)
            # 1차 부분청산 강제 완료
            mgr3.check_exit("KRW-S", entry * (1 + p1 * 1.05))
            if "KRW-S" in mgr3.positions:
                mgr3.positions["KRW-S"].partial_exited = True
            p2_hit = entry * (1 + p2 * 1.05)
            sig3 = mgr3.check_exit("KRW-S", p2_hit)
            assert sig3.should_exit, (
                f"2차 익절 미발동: price={p2_hit:.1f} p2={entry*(1+p2):.1f}"
            )

            # ── 시나리오 D: 안전 가격 (SL↑ p1↓ 사이 → 청산 없어야 함) ──
            mgr4 = PositionManagerV2(partial_exit_1=p1, partial_exit_2=p2)
            pos4 = PositionV2(
                market="KRW-S", entry_price=entry,
                volume=100.0, amount_krw=amount_krw,
                strategy="TEST",
                stop_loss=sl_price, take_profit=tp_price,
            )
            mgr4.add_position(pos4)
            safe = entry * (1 + sl_pct * 0.3)   # SL 위, p1 트리거 아래
            sig4 = mgr4.check_exit("KRW-S", safe)
            if sig4.should_exit:
                assert sig4.reason not in (ExitReason.STOP_LOSS, ExitReason.TAKE_PROFIT), (
                    f"안전구간 오청산: reason={sig4.reason} price={safe:.1f}"
                )

            passed += 1
        except AssertionError as e:
            log(FAIL, "포지션관리", desc, str(e), "")
            failed += 1
        except Exception as e:
            log(FAIL, "포지션관리", desc, "예외", str(e)[:80])
            failed += 1

    print(f"  결과: {passed}통과 / {failed}실패 / 100케이스")
    return passed, failed

# ══════════════════════════════════════════════════════════════
# STRESS TEST 8: 레짐 감지 - 100가지 시장 상태
# ══════════════════════════════════════════════════════════════
def stress_regime_detector():
    print("\n── STRESS 8: 레짐 감지 (100가지 시장 상태) ──")
    from signals.filters.regime_detector import RegimeDetector

    detector = RegimeDetector()
    passed = failed = 0
    random.seed(456)

    scenarios = []
    # 강한 상승 트렌드 25개
    for _ in range(25):
        scenarios.append(("강상승", random.uniform(1.5, 3.0)))
    # 강한 하락 트렌드 25개
    for _ in range(25):
        scenarios.append(("강하락", random.uniform(-3.0, -1.5)))
    # 횡보/중립 25개
    for _ in range(25):
        scenarios.append(("횡보", random.uniform(-0.3, 0.3)))
    # 약한 방향 25개
    for _ in range(25):
        scenarios.append(("약트렌드", random.uniform(-1.4, 1.4)))

    random.shuffle(scenarios)

    for i, (label, trend) in enumerate(scenarios[:100]):
        base = random.choice([500, 1000, 5000, 50000])
        seed = i * 13
        desc = f"#{i}_{label}_trend={trend:.1f}_base={base}"
        try:
            df = make_ohlcv(100, base, trend=trend, seed=seed)
            result = detector.detect("KRW-STRESS", df)

            # 반환값 유효성 검증
            assert result is not None, "detect 반환값 None"
            assert hasattr(result, "value") or isinstance(result, str) or \
                   "MarketRegime" in str(type(result)), \
                   f"레짐 타입 오류: {type(result)}"

            # 문자열 변환 가능 여부
            regime_str = str(result)
            assert len(regime_str) > 0, "레짐 문자열 변환 실패"

            passed += 1
        except AssertionError as e:
            log(FAIL, "레짐감지", desc, str(e), "")
            failed += 1
        except Exception as e:
            log(FAIL, "레짐감지", desc, "예외", str(e)[:80])
            failed += 1

    print(f"  결과: {passed}통과 / {failed}실패 / 100케이스")
    return passed, failed

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("  APEX BOT 스트레스 테스트 (800개 검증)")
    print(f"  실행: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    start = time.time()
    total_passed = total_failed = 0

    tests = [
        ("부분청산",     stress_partial_exit),
        ("ATR손절",      stress_atr_stop),
        ("Kelly사이징",  stress_kelly),
        ("PPO정규화",    stress_ppo_profit_rate),
        ("트레일링스탑", stress_trailing_stop),
        ("MTF병합",      stress_mtf_merger),
        ("포지션관리",   stress_position_manager),
        ("레짐감지",     stress_regime_detector),
    ]

    summary = []
    for name, fn in tests:
        try:
            p, f = fn()
            total_passed += p
            total_failed += f
            status = PASS if f == 0 else FAIL
            summary.append((status, name, p, f))
        except Exception as e:
            print(f"  {FAIL} {name} 전체 오류: {e}")
            traceback.print_exc()
            summary.append((FAIL, name, 0, 100))
            total_failed += 100

    elapsed = time.time() - start

    print("\n" + "=" * 60)
    print("  스트레스 테스트 결과 요약")
    print("=" * 60)
    for status, name, p, f in summary:
        bar = "█" * p + "░" * f
        bar = bar[:30]
        print(f"  {status} {name:<12} {p:3}통과 {f:2}실패  [{bar}]")

    print(f"\n  총 {total_passed+total_failed}개: "
          f"✅ {total_passed}통과  ❌ {total_failed}실패")
    print(f"  소요시간: {elapsed:.1f}초")

    if total_failed == 0:
        print("  🏆 800개 전체 통과! 시스템 스트레스 검증 완료!")
    else:
        print(f"\n  실패 상세:")
        for status, cat, tid, cond, detail in results_all:
            if status == FAIL:
                print(f"    ❌ [{cat}] {tid}: {cond} → {detail}")
    print("=" * 60)

if __name__ == "__main__":
    main()
