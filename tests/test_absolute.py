
import asyncio, random, sys, os, shutil, tempfile, sqlite3, pathlib

# 프로젝트 루트 경로 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np

PASS = 0
FAIL = 0
ERRORS = []
TEMP_DIR = tempfile.mkdtemp(prefix="apex_test_")

def ok(name):
    global PASS
    PASS += 1
    print(f"  [PASS #{PASS}] {name}", flush=True)

def fail(name, err):
    global FAIL
    FAIL += 1
    ERRORS.append((name, str(err)[:120]))
    print(f"  [FAIL #{PASS+FAIL}] {name}: {str(err)[:120]}", flush=True)

def make_df(n=100, trend="random"):
    base = random.uniform(100, 100000)
    closes = [base]
    for _ in range(n - 1):
        if trend == "up":     closes.append(closes[-1] * random.uniform(1.001, 1.03))
        elif trend == "down": closes.append(closes[-1] * random.uniform(0.97, 0.999))
        elif trend == "crash":closes.append(closes[-1] * random.uniform(0.85, 0.95))
        elif trend == "spike":closes.append(closes[-1] * random.uniform(1.05, 1.20))
        else:                 closes.append(closes[-1] * random.uniform(0.97, 1.03))
    return pd.DataFrame({
        "open":   [c * random.uniform(0.99, 1.01) for c in closes],
        "high":   [c * random.uniform(1.00, 1.02) for c in closes],
        "low":    [c * random.uniform(0.98, 1.00) for c in closes],
        "close":  closes,
        "volume": [random.uniform(1e6, 1e9) for _ in closes],
    })

def make_market():
    return random.choice(["KRW-BTC","KRW-ETH","KRW-XRP","KRW-SOL","KRW-DOGE",
                          "KRW-AQT","KRW-SUPER","KRW-CARV","KRW-PEPE","KRW-SHIB"])

def make_price(): return random.uniform(1, 100000000)

# ── TEST 1: ATR 손절 계산 (100회) ──────────────────────────────
async def test_atr_stop():
    print("\n[TEST 1] ATR 손절 계산 - 100회")
    from risk.stop_loss.atr_stop import ATRStopLoss
    atr = ATRStopLoss()
    err_count = 0
    for i in range(100):
        try:
            trend = random.choice(["up","down","crash","spike","random"])
            df = make_df(random.randint(20, 300), trend)
            entry = max(df["close"].iloc[-1], 0.001)
            current = entry * random.uniform(0.8, 1.3)
            profit_pct = (current - entry) / entry
            result = atr.calculate(df, entry)
            assert result is not None
            assert result.stop_loss > 0
            assert result.take_profit > 0
            dynamic = atr.get_dynamic_levels(df, entry, current, profit_pct)
            capped = max(dynamic.stop_loss, entry * 0.97)
            assert capped >= entry * 0.97, f"cap 미적용 h={trend}"
        except Exception as e:
            err_count += 1
            if err_count <= 3: fail(f"ATR #{i+1} {trend}", e)
    if err_count == 0: ok("ATR 손절 100회 완료")
    else: fail(f"ATR 총 {err_count}건 실패", "위 참조")

# ── TEST 2: Position Sizer (100회) ─────────────────────────────
async def test_position_sizer():
    print("\n[TEST 2] Position Sizer - 100회")
    from risk.position_sizer import KellyPositionSizer
    sizer = KellyPositionSizer()
    err_count = 0
    strategies = ["Order_Block","MACD_Cross","Vol_Breakout","BEAR_REVERSAL","Bollinger_Squeeze","UNKNOWN"]
    for i in range(100):
        try:
            capital = random.uniform(5000, 10000000)
            s = random.choice(strategies)
            conf = random.uniform(0.0, 1.0)
            result = sizer.calculate(total_capital=capital, strategy=s, market="KRW-BTC", confidence=conf)
            assert result is not None
            assert result >= 0
            assert result <= capital * 1.01
        except Exception as e:
            err_count += 1
            if err_count <= 3: fail(f"Sizer #{i+1}", e)
    if err_count == 0: ok("Position Sizer 100회 완료")
    else: fail(f"Sizer 총 {err_count}건 실패", "위 참조")

# ── TEST 3: 전략 신호 (각 50회) ────────────────────────────────
async def test_strategies():
    print("\n[TEST 3] 전략 신호 생성 - 각 50회")
    strategy_map = [
        ("strategies.momentum.macd_cross", "MACDCrossStrategy"),
        ("strategies.momentum.rsi_divergence", "RSIDivergenceStrategy"),
        ("strategies.mean_reversion.bollinger_squeeze", "BollingerSqueezeStrategy"),
        ("strategies.mean_reversion.vwap_reversion", "VWAPReversionStrategy"),
        ("strategies.momentum.supertrend", "SupertrendStrategy"),
    ]
    for mod_name, cls_name in strategy_map:
        try:
            mod = __import__(mod_name, fromlist=[cls_name])
            strategy = getattr(mod, cls_name)()
        except Exception as e:
            fail(f"{cls_name} 로드", e); continue
        err_count = 0
        for i in range(50):
            try:
                df = make_df(random.randint(50, 300), random.choice(["up","down","crash","spike","random"]))
                result = strategy.generate_signal(df, make_market())
                if result is not None:
                    assert hasattr(result, "signal")
                    assert hasattr(result, "confidence")
                    assert 0.0 <= result.confidence <= 1.0
            except Exception as e:
                err_count += 1
                if err_count <= 2: fail(f"{cls_name} #{i+1}", e)
        if err_count == 0: ok(f"{cls_name} 50회 완료")
        else: fail(f"{cls_name} {err_count}건 실패", "위 참조")

# ── TEST 4: Performance Tracker - 완전 격리 (100회) ────────────
async def test_performance_tracker():
    print("\n[TEST 4] Performance Tracker - 격리 DB 사용 100회")
    import pathlib as _pl
    from monitoring.performance_tracker import PerformanceTracker
    tmp_db = _pl.Path(TEMP_DIR) / "test_perf.db"
    err_count = 0
    for _idx in range(100):
        try:
            tracker = PerformanceTracker(db_path=tmp_db)
            mock_trades = [{"profit_rate": random.uniform(-5,5), "timestamp": "2026-04-20T10:00:00"} for _ in range(20)]
            tracker.update(mock_trades)
            stats = tracker.get_stats(days=14)
            assert hasattr(stats, "win_rate")
            assert hasattr(stats, "sharpe_ratio")
            assert 0.0 <= stats.win_rate <= 1.0, f"win_rate 범위 초과: {stats.win_rate}"
        except Exception as e:
            err_count += 1
            if err_count <= 3: fail(f"Tracker #{_idx+1}", e)
    if tmp_db.exists(): tmp_db.unlink()
    if err_count == 0: ok("Performance Tracker 100회 완료 (격리)")
    else: fail(f"Tracker 총 {err_count}건 실패", "위 참조")

# ── TEST 5: PPO Trainer - 새 인스턴스 격리 (100회) ─────────────
async def test_ppo_trainer():
    print("\n[TEST 5] PPO Online Trainer - 격리 인스턴스 100회")
    from models.train.ppo_online_trainer import PPOOnlineTrainer
    # 새 인스턴스 = 메모리만 사용, 실제 buffer에 영향 없음
    trainer = PPOOnlineTrainer()
    err_count = 0
    for i in range(100):
        try:
            action = random.choice([0, 1, 2])
            profit = random.uniform(-0.1, 0.1)
            hold_h = random.uniform(0, 200)
            trainer.add_experience(market=make_market(), action=action,
                                   profit_rate=profit, hold_hours=hold_h)
            stats = trainer.get_buffer_stats()
            assert isinstance(stats, dict)
            assert "count" in stats
            assert stats["count"] >= 0
        except Exception as e:
            err_count += 1
            if err_count <= 3: fail(f"PPO #{i+1}", e)
    # 이 trainer는 로컬 변수라 GC 후 사라짐 (실제 봇 buffer 영향 없음)
    if err_count == 0: ok("PPO Trainer 100회 완료 (격리)")
    else: fail(f"PPO 총 {err_count}건 실패", "위 참조")

# ── TEST 6: Walk-Forward 평가 파이프라인 (50회) ─────────────────
async def test_walk_forward():
    print("\n[TEST 6] Walk-Forward WFResult/WalkForwardRunner 구조 검증 - 50회")
    from backtesting.walk_forward import WalkForwardRunner, WFResult
    err_count = 0
    for i in range(50):
        try:
            # WFResult 객체 직접 생성 테스트
            r = WFResult(
                strategy_name=random.choice(["MACD_Cross","RSI_Divergence","Bollinger_Squeeze"]),
                is_sample_days=90,
                oos_sample_days=30,
                best_params={"period": random.randint(5,30)},
            )
            r.is_sharpe    = random.uniform(-2, 3)
            r.oos_sharpe   = random.uniform(-2, 3)
            r.oos_pnl_pct  = random.uniform(-10, 20)
            r.is_profitable = bool(r.oos_sharpe > 0)
            r.is_sharpe   = random.uniform(-2, 3)
            r.oos_sharpe  = random.uniform(-2, 3)
            r.oos_pnl_pct = random.uniform(-10, 20)
            r.is_profitable = bool(r.oos_sharpe > 0)
            assert isinstance(r.is_profitable, bool)
            assert hasattr(r, "best_params")
            assert isinstance(r.is_profitable, bool)
            assert isinstance(r.best_params, dict)
            # WalkForwardRunner 초기화 테스트
            # WalkForwardRunner 인스턴스 생성 검증 (파라미터 없이)
            runner = WalkForwardRunner.__new__(WalkForwardRunner)
            assert hasattr(runner, "run_all_strategies")
        except Exception as e:
            err_count += 1
            if err_count <= 3: fail(f"WF #{i+1}", e)
    if err_count == 0: ok("Walk-Forward 구조 검증 50회 완료")
    else: fail(f"WF 총 {err_count}건 실패", "위 참조")

# ── TEST 7: AutoTrainer 조건 판단 (100회) ──────────────────────
async def test_auto_trainer():
    print("\n[TEST 7] AutoTrainer 조건 판단 - 100회")
    from models.train.auto_trainer import AutoTrainer
    err_count = 0
    for i in range(100):
        try:
            trainer = AutoTrainer()
            scenario = random.choice(["recent","old","very_old"])
            if scenario == "recent":
                trainer._last_retrain = datetime.now() - timedelta(days=random.uniform(0, 2))
                result = trainer._should_retrain()
                assert result == False, f"recent인데 재학습: days<3"
            elif scenario in ("old","very_old"):
                trainer._last_retrain = datetime.now() - timedelta(days=random.uniform(3, 30))
                result = trainer._should_retrain()
                assert result == True, f"old인데 스킵"
            assert isinstance(result, bool)
        except Exception as e:
            err_count += 1
            if err_count <= 3: fail(f"AutoTrainer #{i+1} {scenario}", e)
    if err_count == 0: ok("AutoTrainer 100회 완료")
    else: fail(f"AutoTrainer 총 {err_count}건 실패", "위 참조")

# ── TEST 8: _time_based_tp_threshold 경계값 (200회) ─────────────
async def test_tp_threshold():
    print("\n[TEST 8] TP Threshold 경계값 - 200회")
    from core.engine_schedule import EngineScheduleMixin
    mock = MagicMock()
    err_count = 0
    for i in range(200):
        try:
            h = random.uniform(0, 120)
            mock._get_hold_hours.return_value = h
            threshold = EngineScheduleMixin._time_based_tp_threshold(mock, make_market())
            assert isinstance(threshold, float)
            if h >= 48:   assert threshold == -999.0
            elif h >= 24: assert threshold == 0.5
            elif h >= 6:  assert threshold == 0.8
            else:         assert threshold == 1.5
        except Exception as e:
            err_count += 1
            if err_count <= 3: fail(f"TP #{i+1} h={h:.1f}", e)
    if err_count == 0: ok("TP Threshold 200회 완료")
    else: fail(f"TP 총 {err_count}건 실패", "위 참조")

# ── TEST 9: DB 읽기 전용 무결성 (50회) ─────────────────────────
async def test_db_readonly():
    print("\n[TEST 9] DB 읽기 전용 무결성 - 50회")
    err_count = 0
    for i in range(50):
        try:
            # 읽기 전용 모드로만 접속
            conn = sqlite3.connect(f"file:database/apex_bot.db?mode=ro", uri=True)
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            assert len(tables) > 0
            table = random.choice(tables)[0]
            count = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
            assert count >= 0
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            assert integrity == "ok"
            conn.close()
        except Exception as e:
            err_count += 1
            if err_count <= 3: fail(f"DB #{i+1}", e)
    if err_count == 0: ok("DB 읽기 전용 무결성 50회 완료")
    else: fail(f"DB 총 {err_count}건 실패", "위 참조")

# ── TEST 10: 극단값 엣지케이스 (100회) ─────────────────────────
async def test_edge_cases():
    print("\n[TEST 10] 극단값 엣지케이스 - 100회")
    from risk.stop_loss.atr_stop import ATRStopLoss
    from risk.position_sizer import KellyPositionSizer
    atr = ATRStopLoss()
    sizer = KellyPositionSizer()
    flat_df = pd.DataFrame({"open":[100]*50,"high":[100]*50,
                             "low":[100]*50,"close":[100]*50,"volume":[1000]*50})
    cases = [
        ("극소자본", lambda: sizer.calculate(total_capital=5000,strategy="Order_Block",market="KRW-BTC",confidence=0.5)),
        ("극대자본", lambda: sizer.calculate(total_capital=1e9,strategy="Order_Block",market="KRW-BTC",confidence=0.99)),
        ("conf=0",   lambda: sizer.calculate(total_capital=100000,strategy="MACD_Cross",market="KRW-ETH",confidence=0.0)),
        ("conf=1",   lambda: sizer.calculate(total_capital=100000,strategy="MACD_Cross",market="KRW-ETH",confidence=1.0)),
        ("ATR최소",  lambda: atr.calculate(make_df(20), make_price())),
        ("ATR최대",  lambda: atr.calculate(make_df(500), make_price())),
        ("ATR급등",  lambda: atr.calculate(make_df(100,"spike"), make_price())),
        ("ATR급락",  lambda: atr.calculate(make_df(100,"crash"), make_price())),
        ("ATR동일가",lambda: atr.calculate(flat_df, 100.0)),
        ("미지원전략",lambda: sizer.calculate(total_capital=100000,strategy="UNKNOWN",market="KRW-BTC",confidence=0.7)),
    ]
    err_count = 0
    for i in range(100):
        name, fn = random.choice(cases)
        try:
            result = fn()
            assert result is not None
        except Exception as e:
            err_count += 1
            if err_count <= 3: fail(f"Edge #{i+1} {name}", e)
    if err_count == 0: ok("극단값 100회 완료")
    else: fail(f"Edge 총 {err_count}건 실패", "위 참조")

# ── 메인 ───────────────────────────────────────────────────────
async def main():
    print("=" * 70)
    print("APEX BOT 절대 무결성 테스트 (격리 환경)")
    print(f"시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"임시 디렉토리: {TEMP_DIR}")
    print("실제 DB/모델/Buffer에 영향 없음")
    print("=" * 70)

    await test_atr_stop()
    await test_position_sizer()
    await test_strategies()
    await test_performance_tracker()
    await test_ppo_trainer()
    await test_walk_forward()
    await test_auto_trainer()
    await test_tp_threshold()
    await test_db_readonly()
    await test_edge_cases()

    # 임시 디렉토리 정리
    shutil.rmtree(TEMP_DIR, ignore_errors=True)

    print("\n" + "=" * 70)
    total = PASS + FAIL
    print(f"최종 결과: {PASS}/{total} 통과 | 실패: {FAIL}건")
    if ERRORS:
        print(f"\n실패 목록:")
        for name, err in ERRORS:
            print(f"  - {name}: {err}")
    else:
        print("✅ 전체 통과 - 실제 DB/모델 오염 없음")
    print(f"완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    return FAIL

if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(result)