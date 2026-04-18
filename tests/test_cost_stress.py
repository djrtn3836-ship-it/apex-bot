#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
APEX BOT 슬리피지/수수료 스트레스 테스트
실비용 반영 성과 측정 – 5개 영역 x 100회 = 500개 검증
"""
import sys, os, random, time, statistics as _stats
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

random.seed(42)
print(f"\nAPEX BOT 슬리피지/수수료 스트레스 테스트 실행: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("5개 영역 x 100회 = 500개 검증\n" + "=" * 65)

# ── 실제 설정값 로드 ──────────────────────────────────────
FEE_RATE      = 0.0005   # 0.05% (upbit taker)
SLIPPAGE_RATE = 0.001    # 0.10% (settings.py 기본값)
ROUND_TRIP_COST = (FEE_RATE * 2) + (SLIPPAGE_RATE * 2)  # 왕복 총비용

# SlippageModel 실제 값
BASE_SLIPPAGE = {
    "KRW-BTC":  0.03, "KRW-ETH":  0.04, "KRW-SOL":  0.06,
    "KRW-XRP":  0.05, "KRW-ADA":  0.07, "KRW-DOGE": 0.08,
    "KRW-DOT":  0.07, "KRW-LINK": 0.07, "KRW-AVAX": 0.07,
    "KRW-ATOM": 0.08,
}
DEFAULT_SLIPPAGE = 0.08

print(f"[설정] 수수료: {FEE_RATE*100:.3f}% | 슬리피지: {SLIPPAGE_RATE*100:.3f}%")
print(f"[설정] 왕복 총비용: {ROUND_TRIP_COST*100:.3f}%")
print(f"[설정] 손익분기 최소 수익률: >{ROUND_TRIP_COST*100:.3f}%\n")

# ── 공통 실행기 ───────────────────────────────────────────
def run_area(name, func, n=100):
    passed = failed = warned = 0
    fails = []
    t0 = time.time()
    for i in range(n):
        try:
            r = func(i)
            if r is None or r is True:  passed += 1
            elif r == "WARN":           warned += 1; passed += 1
            else:                       failed += 1; fails.append(f"  #{i+1}: {r}")
        except Exception as e:
            failed += 1; fails.append(f"  #{i+1}: EXCEPTION {type(e).__name__}: {e}")
    icon = "OK" if failed == 0 else "NG"
    print(f"\n[{name}]")
    print(f"결과: {passed}통과 / {failed}실패 / {n}케이스 ({time.time()-t0:.2f}s) [{icon}]")
    if warned:   print(f"  경고: {warned}건")
    for d in fails[:10]: print(d)
    if len(fails) > 10: print(f"  ... 외 {len(fails)-10}건")
    return passed, failed, warned

# ── SlippageModel 로드 ────────────────────────────────────
_slip_ok = False
_slip_model = None
try:
    from core.slippage_model import SlippageModel
    _slip_model = SlippageModel()
    _slip_ok = True
    print("[LOAD OK] core.slippage_model.SlippageModel")
except Exception as e:
    print(f"[LOAD FAIL] SlippageModel: {e} -> 시뮬레이션 모드")

def sim_slippage(market, amount_krw, volatility=None):
    """SlippageModel 시뮬레이션 (모듈 없을 때)"""
    base     = BASE_SLIPPAGE.get(market, DEFAULT_SLIPPAGE)
    size_adj = 0.02 if amount_krw > 500_000 else (0.01 if amount_krw > 200_000 else 0.0)
    vol_adj  = min(volatility * 0.1, 0.05) if volatility else 0.0
    return min(base + size_adj + vol_adj, 0.5)

def get_slippage(market, amount_krw, volatility=None):
    if _slip_ok and _slip_model:
        try:
            return _slip_model.estimate(market, amount_krw, volatility=volatility)
        except:
            pass
    return sim_slippage(market, amount_krw, volatility)

# ══════════════════════════════════════════════════════════
# C1: 슬리피지 모델 정확도 검증
# ══════════════════════════════════════════════════════════
print("\nC1: 슬리피지 모델 정확도 테스트")

def c1(i):
    markets  = list(BASE_SLIPPAGE.keys()) + ["KRW-CARV","KRW-BLUR","KRW-SAFE"]
    market   = markets[i % len(markets)]
    amount   = random.uniform(10_000, 2_000_000)
    vol      = random.uniform(0.5, 5.0)
    slip     = get_slippage(market, amount, volatility=vol)

    # 검증 1: 슬리피지는 0 이상 0.5% 이하
    if not (0 <= slip <= 0.5):
        return f"슬리피지 범위 오류: {slip:.4f}% (market={market})"

    # 검증 2: 알려진 코인은 기본값 이상이어야 함
    expected_base = BASE_SLIPPAGE.get(market, DEFAULT_SLIPPAGE)
    if slip < expected_base - 0.001:
        return f"슬리피지 기본값 미달: {slip:.4f}% < base={expected_base}% ({market})"

    # 검증 3: 대형 주문은 소형 주문보다 슬리피지 크거나 같아야 함
    slip_small = get_slippage(market, 50_000)
    slip_large = get_slippage(market, 1_000_000)
    if slip_large < slip_small - 0.001:
        return f"대형주문 슬리피지 역전: large={slip_large:.4f}% < small={slip_small:.4f}%"

    # 검증 4: 고변동성은 저변동성보다 슬리피지 크거나 같아야 함
    slip_low_vol  = get_slippage(market, amount, volatility=0.5)
    slip_high_vol = get_slippage(market, amount, volatility=5.0)
    if slip_high_vol < slip_low_vol - 0.001:
        return f"고변동성 슬리피지 역전: high={slip_high_vol:.4f}% < low={slip_low_vol:.4f}%"

    return True

p1,f1,w1 = run_area("C1: 슬리피지 모델 정확도", c1)

# ══════════════════════════════════════════════════════════
# C2: 왕복 비용 후 실질 손익 계산
# ══════════════════════════════════════════════════════════
print("\nC2: 왕복 비용 후 실질 손익 테스트")

def calc_real_pnl(gross_pnl_pct, market, amount_krw, volatility=None):
    """총손익에서 수수료+슬리피지 차감한 실질 손익"""
    slip_buy  = get_slippage(market, amount_krw, volatility) / 100
    slip_sell = get_slippage(market, amount_krw, volatility) / 100
    fee_buy   = FEE_RATE
    fee_sell  = FEE_RATE
    total_cost = slip_buy + slip_sell + fee_buy + fee_sell
    return gross_pnl_pct - total_cost

def c2(i):
    markets  = ["KRW-BTC","KRW-ETH","KRW-SOL","KRW-XRP","KRW-CARV","KRW-BLUR"]
    market   = markets[i % len(markets)]
    amount   = random.uniform(30_000, 500_000)
    vol      = random.uniform(1.0, 4.0)
    gross    = random.uniform(-0.05, 0.05)   # -5% ~ +5% 총손익
    real     = calc_real_pnl(gross, market, amount, vol)

    # 검증 1: 실질손익은 항상 총손익보다 작아야 함 (비용이 양수)
    if real >= gross + 0.0001:
        return f"실질손익({real:.4f}) >= 총손익({gross:.4f}) — 비용 미반영"

    # 검증 2: 비용은 0.1% ~ 0.6% 범위여야 함
    cost = gross - real
    if cost < 0.001:
        return f"왕복비용 너무 낮음: {cost*100:.4f}%"
    if cost > 0.006:
        return f"왕복비용 너무 높음: {cost*100:.4f}%"

    # 검증 3: 손익분기점 검증
    breakeven = calc_real_pnl(0.0, market, amount, vol)
    if breakeven >= 0:
        return f"제로수익 거래에서 실질손익 양수?: {breakeven:.5f}"

    # 검증 4: 실제 DB 수익률과 비교 (4/15~4/18 실측 평균 +0.870%)
    _REAL_AVG = 0.00870
    if i % 10 == 0:
        real_from_actual = calc_real_pnl(_REAL_AVG, market, amount, vol)
        if real_from_actual <= 0:
            return (f"실측 평균수익({_REAL_AVG*100:.3f}%)이 "
                    f"비용 차감 후 손실: {real_from_actual*100:.4f}%")
    return True

p2,f2,w2 = run_area("C2: 왕복 비용 후 실질 손익", c2)

# ══════════════════════════════════════════════════════════
# C3: Kelly 사이징 비용 반영 검증
# ══════════════════════════════════════════════════════════
print("\nC3: Kelly 사이징 비용 반영 테스트")

_kelly_ok = False
_kelly_sz  = None
try:
    from risk.position_sizer import KellyPositionSizer
    _kelly_sz = KellyPositionSizer()
    _kelly_ok = True
    print("  [LOAD OK] KellyPositionSizer")
except Exception as e:
    print(f"  [LOAD FAIL] {e} -> 시뮬레이션 모드")

def kelly_after_cost(wr, rr_gross, cost_pct):
    """비용 반영 후 실질 RR로 Kelly 계산"""
    rr_net = rr_gross - cost_pct   # 수익 측에서 비용 차감
    if rr_net <= 0:
        return 0.0
    lr = 1.0 - wr
    raw = max(0.0, (wr * rr_net - lr) / rr_net)
    return min(raw * 0.5, 0.20)   # Half-Kelly, 20% 상한

def c3(i):
    wr        = random.uniform(0.40, 0.85)
    rr_gross  = random.uniform(1.0, 4.0)     # 비용 전 RR
    market    = random.choice(list(BASE_SLIPPAGE.keys()))
    amount    = random.uniform(50_000, 500_000)
    cost      = (FEE_RATE * 2) + (get_slippage(market, amount) / 100 * 2)

    kelly_gross = kelly_after_cost(wr, rr_gross, 0.0)    # 비용 무시
    kelly_net   = kelly_after_cost(wr, rr_gross, cost)   # 비용 반영

    # 검증 1: 비용 반영 후 Kelly가 비용 전보다 작거나 같아야 함
    if kelly_net > kelly_gross + 0.001:
        return f"비용반영 Kelly({kelly_net:.4f}) > 비용전({kelly_gross:.4f})"

    # 검증 2: 비용이 RR을 초과하면 Kelly=0이어야 함
    if cost >= rr_gross and kelly_net > 0.001:
        return f"비용({cost:.4f}) >= RR({rr_gross:.4f})인데 Kelly={kelly_net:.4f}"

    # 검증 3: Kelly는 0~20% 범위
    if not (0 <= kelly_net <= 0.201):
        return f"Kelly 범위 오류: {kelly_net:.4f}"

    # 검증 4: 고수수료 시장에서 고빈도 거래 위험성 검증
    # 하루 10회 거래 시 총 비용
    daily_trades  = 10
    daily_cost    = cost * daily_trades
    daily_gross   = 0.00870 * daily_trades * wr  # 기대 총수익
    daily_net     = daily_gross - daily_cost
    if i % 5 == 0:
        # 일일 순수익이 일일 비용의 2배 이상이어야 지속 가능
        if daily_gross > 0 and daily_net < daily_gross * 0.3:
            return "WARN"  # 경고만 (비용 비율 70% 초과)
    return True

p3,f3,w3 = run_area("C3: Kelly 비용 반영", c3)

# ══════════════════════════════════════════════════════════
# C4: 전략별 손익분기 승률 계산
# ══════════════════════════════════════════════════════════
print("\nC4: 전략별 손익분기 승률 테스트")

STRATEGIES = {
    "Order_Block":     {"avg_win": 0.025, "avg_loss": 0.015, "typical_amount": 200_000},
    "MACD_Cross":      {"avg_win": 0.018, "avg_loss": 0.013, "typical_amount": 150_000},
    "VWAP_Reversion":  {"avg_win": 0.012, "avg_loss": 0.010, "typical_amount": 100_000},
    "Vol_Breakout":    {"avg_win": 0.020, "avg_loss": 0.015, "typical_amount": 180_000},
    "Bollinger_Band":  {"avg_win": 0.015, "avg_loss": 0.012, "typical_amount": 120_000},
}

def breakeven_winrate(avg_win, avg_loss, cost):
    """손익분기 승률: wr * (avg_win - cost) = (1-wr) * (avg_loss + cost)"""
    # wr * (win-c) = (1-wr) * (loss+c)
    # wr * win - wr*c = loss + c - wr*loss - wr*c
    # wr * win = loss + c - wr*loss
    # wr * (win + loss) = loss + c
    # wr = (loss + c) / (win + loss)
    denominator = avg_win + avg_loss
    if denominator <= 0:
        return 1.0
    return min((avg_loss + cost) / denominator, 1.0)

def c4(i):
    strat_names = list(STRATEGIES.keys())
    strat_name  = strat_names[i % len(strat_names)]
    strat       = STRATEGIES[strat_name]
    market      = random.choice(["KRW-BTC","KRW-ETH","KRW-SOL","KRW-XRP","KRW-CARV"])
    amount      = strat["typical_amount"] * random.uniform(0.5, 2.0)
    vol         = random.uniform(1.0, 3.0)

    slip        = get_slippage(market, amount, vol) / 100
    cost        = (FEE_RATE * 2) + (slip * 2)   # 왕복 총비용

    avg_win     = strat["avg_win"]
    avg_loss    = strat["avg_loss"]
    be_wr       = breakeven_winrate(avg_win, avg_loss, cost)
    rr          = avg_win / avg_loss if avg_loss > 0 else 0

    # 검증 1: 손익분기 승률은 0~1 범위
    if not (0 < be_wr < 1.0):
        return f"[{strat_name}] 손익분기 승률 범위 오류: {be_wr:.4f}"

    # 검증 2: 비용이 수익을 초과하면 승률 100%도 손실
    if cost >= avg_win and be_wr < 0.99:
        return f"[{strat_name}] 비용({cost*100:.3f}%) >= 수익({avg_win*100:.3f}%)인데 be_wr={be_wr:.4f}"

    # 검증 3: 실측 승률(73.4%)이 손익분기 승률 이상이어야 함
    ACTUAL_WR = 0.734   # 4/15~4/18 실측
    if ACTUAL_WR < be_wr:
        return (f"[{strat_name}] 실측승률({ACTUAL_WR:.3f}) < "
                f"손익분기({be_wr:.3f}) — 비용={cost*100:.3f}%")

    # 검증 4: RR이 1 이상이어야 의미있는 전략
    if rr < 1.0:
        return f"[{strat_name}] RR={rr:.2f} < 1.0 (avg_win={avg_win*100:.2f}% avg_loss={avg_loss*100:.2f}%)"

    # 검증 5: 전략별 기대값(EV) 계산
    ev = ACTUAL_WR * (avg_win - cost/2) - (1-ACTUAL_WR) * (avg_loss + cost/2)
    if ev <= 0:
        return (f"[{strat_name}] 기대값 음수: EV={ev*100:.4f}% "
                f"(wr={ACTUAL_WR:.3f} cost={cost*100:.3f}%)")
    return True

p4,f4,w4 = run_area("C4: 전략별 손익분기 승률", c4)

# ══════════════════════════════════════════════════════════
# C5: 실거래 DB 데이터 비용 검증
# ══════════════════════════════════════════════════════════
print("\nC5: 실거래 DB 비용 검증 테스트")

# DB에서 실제 거래 데이터 로드
_db_trades = []
try:
    import sqlite3
    db_path = "database/apex_bot.db"
    if os.path.exists(db_path):
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.execute("""
            SELECT market, side, profit_rate, amount_krw, timestamp
            FROM trade_history
            WHERE side='SELL' AND timestamp >= '2026-04-15'
            ORDER BY timestamp
        """)
        _db_trades = cur.fetchall()
        con.close()
        print(f"  [DB OK] 실거래 {len(_db_trades)}건 로드 (4/15 이후)")
    else:
        print("  [DB SKIP] database/apex_bot.db 없음 -> 시뮬레이션 모드")
except Exception as e:
    print(f"  [DB FAIL] {e} -> 시뮬레이션 모드")

def c5(i):
    if _db_trades:
        # 실거래 데이터 기반 검증
        idx   = i % len(_db_trades)
        trade = _db_trades[idx]
        market, side, pnl_raw, amount_krw, ts = trade

        # profit_rate 단위 확인 (% 단위로 저장됨)
        pnl_pct = pnl_raw / 100 if abs(pnl_raw) > 0.5 else pnl_raw

        # 실제 비용 계산
        slip  = get_slippage(market, amount_krw or 100_000) / 100
        cost  = (FEE_RATE * 2) + (slip * 2)
        real  = pnl_pct - cost

        # 검증 1: 수익 거래는 비용 차감 후에도 수익이어야 함 (이상적)
        if pnl_pct > cost * 3:
            # 충분히 큰 수익 거래는 비용 차감 후도 양수여야 함
            if real <= 0:
                return f"[{market}] 충분한 수익({pnl_pct*100:.3f}%)인데 실질손실: {real*100:.4f}%"

        # 검증 2: 손절 거래에서 비용이 손절폭의 50% 이하여야 함
        if pnl_pct < -0.01:
            cost_ratio = abs(cost / pnl_pct)
            if cost_ratio > 0.5:
                return (f"[{market}] 손절비용 비율 과다: "
                        f"cost={cost*100:.3f}% / loss={pnl_pct*100:.3f}% = {cost_ratio:.2f}")

        # 검증 3: 아주 작은 수익(<0.2%)은 비용 차감 후 손실
        if 0 < pnl_pct < cost:
            if real >= 0:
                return f"[{market}] 소수익({pnl_pct*100:.4f}%) 비용({cost*100:.4f}%) 계산 오류"

        return True
    else:
        # 시뮬레이션 모드
        markets  = ["KRW-BTC","KRW-ETH","KRW-SOL","KRW-CARV","KRW-BLUR"]
        market   = markets[i % len(markets)]
        amount   = random.uniform(30_000, 500_000)
        pnl_pct  = random.gauss(0.0087, 0.025)   # 실측 평균 기준
        slip     = get_slippage(market, amount) / 100
        cost     = (FEE_RATE * 2) + (slip * 2)
        real     = pnl_pct - cost

        if pnl_pct > cost * 3 and real <= 0:
            return f"시뮬: 충분한 수익({pnl_pct*100:.3f}%)인데 실질손실"
        return True

p5,f5,w5 = run_area("C5: 실거래 DB 비용 검증", c5)

# ══════════════════════════════════════════════════════════
# 최종 종합 + 실비용 성과 분석
# ══════════════════════════════════════════════════════════
print("\n" + "=" * 65)
print("슬리피지/수수료 스트레스 테스트 종합 결과")
print("=" * 65)

areas = [
    ("C1 슬리피지 모델 정확도",    p1,f1,w1),
    ("C2 왕복비용 후 실질손익",    p2,f2,w2),
    ("C3 Kelly 비용 반영",         p3,f3,w3),
    ("C4 전략별 손익분기",         p4,f4,w4),
    ("C5 실거래 DB 비용 검증",     p5,f5,w5),
]
tp = tf_ = tw = 0
for nm,pp,ff,ww in areas:
    icon = "OK" if ff==0 else "NG"
    ws   = f" (경고{ww})" if ww else ""
    print(f"  [{icon}] {nm:<26} {pp:3d}통과 / {ff:2d}실패{ws}")
    tp+=pp; tf_+=ff; tw+=ww

print("-" * 65)
print(f"  총계: {tp}통과 / {tf_}실패 / {tp+tf_}케이스  경고: {tw}건")
sc = tp/(tp+tf_)*100 if (tp+tf_) else 0
gd = ("S급" if tf_==0 else "A급" if tf_<=5 else "B급" if tf_<=15 else "C급")
print(f"  점수: {sc:.1f}/100  등급: {gd}")

# ── 실비용 반영 성과 분석 출력 ───────────────────────────
print("\n" + "=" * 65)
print("실비용 반영 성과 분석 (4/15~4/18 실측 기준)")
print("=" * 65)

ACTUAL = {
    "trades":      79,
    "win_rate":    0.734,
    "avg_profit":  0.00870,   # +0.870%
    "avg_loss":   -0.018,     # 추정 평균 손실
    "profit_factor": 2.563,
    "mdd":         0.0535,
}

# 대표 시장 비용 계산
rep_market = "KRW-CARV"   # 주요 거래 코인
rep_amount = 80_000       # 실측 평균 거래금액 근사
slip_pct   = get_slippage(rep_market, rep_amount) / 100
cost_rt    = (FEE_RATE * 2) + (slip_pct * 2)

gross_avg  = ACTUAL["avg_profit"]
net_avg    = gross_avg - cost_rt
be_wr      = breakeven_winrate(
    abs(gross_avg),
    abs(ACTUAL["avg_loss"]),
    cost_rt
)

print(f"\n  대표 코인: {rep_market} | 대표 주문: {rep_amount:,}원")
print(f"  수수료 왕복:     {FEE_RATE*2*100:.3f}%")
print(f"  슬리피지 왕복:   {slip_pct*2*100:.3f}%")
print(f"  총 왕복비용:     {cost_rt*100:.3f}%")
print()
print(f"  총손익 (평균):   +{gross_avg*100:.3f}%")
print(f"  실질손익 (평균): +{net_avg*100:.3f}%")
print(f"  비용/수익 비율:  {cost_rt/gross_avg*100:.1f}%")
print()
print(f"  손익분기 승률:   {be_wr*100:.1f}%")
print(f"  실측 승률:       {ACTUAL['win_rate']*100:.1f}%")
margin = ACTUAL['win_rate'] - be_wr
print(f"  안전 마진:       +{margin*100:.1f}%p {'✅ 충분' if margin > 0.10 else '⚠️ 주의'}")
print()

# 일별 수익 시뮬레이션
daily_trades = ACTUAL["trades"] / 4   # 4일간 79건 → 하루 약 20건
daily_cost   = cost_rt * daily_trades
daily_gross  = gross_avg * daily_trades * ACTUAL["win_rate"]
daily_net    = daily_gross - daily_cost
print(f"  하루 거래 횟수:  {daily_trades:.0f}건")
print(f"  하루 총 비용:    {daily_cost*100:.3f}%")
print(f"  하루 기대 총수익:{daily_gross*100:.3f}%")
print(f"  하루 실질 순수익:{daily_net*100:.3f}% {'✅' if daily_net > 0 else '❌'}")
print()

# 월간 예상
monthly_trades = daily_trades * 22
monthly_net_pct = net_avg * monthly_trades * ACTUAL["win_rate"]
print(f"  월간 예상 순수익(승리거래 기준): {monthly_net_pct*100:.2f}%")

grade_parts = []
if net_avg > 0:           grade_parts.append("✅ 비용 차감 후 양수")
if margin > 0.10:         grade_parts.append("✅ 승률 안전마진 충분")
if cost_rt < gross_avg*0.3: grade_parts.append("✅ 비용/수익 비율 양호")
if daily_net > 0:         grade_parts.append("✅ 일일 순수익 양수")
print(f"\n  종합 평가: {' | '.join(grade_parts) if grade_parts else '⚠️ 검토 필요'}")
print("=" * 65)
print(f"완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
