#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
APEX BOT Monte Carlo v2 - 현실적 고정금액 기반
매 거래 고정 금액 투자 (복리 비현실 가정 제거)
"""
import sys, os, random, statistics as _stats, math
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

random.seed(None)
print(f"\nAPEX BOT Monte Carlo v2 (현실적 고정금액 기반)")
print(f"실행: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 65)

# ── 실거래 데이터 로드 ────────────────────────────────────
import sqlite3
_trades = []
try:
    con = sqlite3.connect("database/apex_bot.db")
    cur = con.cursor()
    cur.execute("""
        SELECT profit_rate, amount_krw
        FROM trade_history
        WHERE side='SELL' AND timestamp >= '2026-04-15'
        ORDER BY timestamp
    """)
    _trades = cur.fetchall()
    con.close()
    print(f"[DB] 실거래 {len(_trades)}건 로드")
except Exception as e:
    print(f"[DB FAIL] {e}")

# ── 실측 통계 ─────────────────────────────────────────────
FEE_COST   = 0.0026
rates_raw  = [r[0] for r in _trades]          # % 단위
amounts    = [r[1] for r in _trades if r[1] > 0]

# 실제 거래 금액 통계
avg_amount = _stats.mean(amounts) if amounts else 70000
med_amount = _stats.median(amounts) if amounts else 70000

# 순손익 (% → 소수, 비용 차감)
net_rates  = [(r/100) - FEE_COST for r in rates_raw]
wins       = [r for r in net_rates if r > 0]
losses     = [r for r in net_rates if r <= 0]
n_total    = len(net_rates)
wr         = len(wins) / n_total if n_total else 0
avg_w      = _stats.mean(wins)   if wins   else 0
avg_l      = _stats.mean(losses) if losses else 0
vol_w      = _stats.stdev(wins)  if len(wins)  > 1 else abs(avg_w)*0.5
vol_l      = _stats.stdev(losses)if len(losses)> 1 else abs(avg_l)*0.5

print(f"\n[실측 기준값]")
print(f"  거래수:        {n_total}건")
print(f"  승률:          {wr*100:.1f}%")
print(f"  평균 거래금액: {avg_amount:,.0f}원 (중앙값: {med_amount:,.0f}원)")
print(f"  평균수익:      {avg_w*100:+.3f}% = {avg_w*avg_amount:+,.0f}원/건")
print(f"  평균손실:      {avg_l*100:+.3f}% = {avg_l*avg_amount:+,.0f}원/건")
print(f"  왕복비용:      {FEE_COST*100:.3f}%")

# ── 현실적 시뮬레이션 설정 ────────────────────────────────
N_SIMS         = 1000
DAYS           = 30         # 시뮬레이션 기간
TRADES_PER_DAY = 20         # 하루 거래 수 (실측 기반)
N_TRADES       = DAYS * TRADES_PER_DAY  # 600건
MAX_POSITIONS  = 5          # 동시 최대 포지션
TRADE_AMOUNT   = med_amount # 건당 고정 투자금액

print(f"\n[시뮬레이션 설정]")
print(f"  시뮬레이션:    {N_SIMS:,}회")
print(f"  기간:          {DAYS}일")
print(f"  하루 거래수:   {TRADES_PER_DAY}건")
print(f"  총 거래수:     {N_TRADES}건")
print(f"  건당 투자금:   {TRADE_AMOUNT:,.0f}원 (고정)")
print(f"  동시 포지션:   최대 {MAX_POSITIONS}개")

# ══════════════════════════════════════════════════════════
# 현실적 Monte Carlo: 고정 금액 투자
# ══════════════════════════════════════════════════════════
def simulate_fixed(n_trades, wr, avg_win, avg_loss,
                   vol_win, vol_loss, trade_amount):
    """
    현실적 시뮬레이션:
    - 매 거래 고정 금액 투자
    - 수익/손실은 KRW 절대금액으로 누적
    - 복리 없음 (자본 증가해도 투자금 고정)
    """
    total_pnl  = 0.0
    peak_pnl   = 0.0
    max_dd_krw = 0.0
    pnl_list   = []

    for _ in range(n_trades):
        if random.random() < wr:
            rate = random.gauss(avg_win,  vol_win)
            rate = max(rate, 0.0001)
        else:
            rate = random.gauss(avg_loss, vol_loss)
            rate = min(rate, -0.001)
            rate = max(rate, -0.025)   # SL 최적화 상한 2.5%

        trade_pnl  = trade_amount * rate
        total_pnl += trade_pnl
        pnl_list.append(trade_pnl)

        if total_pnl > peak_pnl:
            peak_pnl = total_pnl
        dd = peak_pnl - total_pnl
        if dd > max_dd_krw:
            max_dd_krw = dd

    sharpe = (_stats.mean(pnl_list) / _stats.stdev(pnl_list)
              if len(pnl_list)>1 and _stats.stdev(pnl_list)>0 else 0)
    return total_pnl, max_dd_krw, sharpe

# ══════════════════════════════════════════════════════════
# 3가지 시나리오
# ══════════════════════════════════════════════════════════
scenarios = [
    ("현재 시스템",   wr,      avg_w,      avg_l,      "실측 그대로"),
    ("보수적 -10%",   wr*0.90, avg_w*0.90, avg_l*1.10, "승률/수익 10% 감소"),
    ("낙관적 +10%",   min(wr*1.05,0.95), avg_w*1.10, avg_l*0.90, "승률/수익 10% 증가"),
]

print(f"\n{'='*65}")
all_results = {}

for scen_name, s_wr, s_aw, s_al, s_desc in scenarios:
    pnl_list   = []
    dd_list    = []
    sharpe_list= []
    loss_runs  = 0   # 손실로 끝난 시뮬레이션

    for _ in range(N_SIMS):
        pnl, dd, sharpe = simulate_fixed(
            N_TRADES, s_wr, s_aw, s_al,
            vol_w, vol_l, TRADE_AMOUNT
        )
        pnl_list.append(pnl)
        dd_list.append(dd)
        sharpe_list.append(sharpe)
        if pnl < 0:
            loss_runs += 1

    pnl_list.sort()
    dd_list.sort()

    p5   = pnl_list[int(N_SIMS*0.05)]
    p25  = pnl_list[int(N_SIMS*0.25)]
    p50  = pnl_list[int(N_SIMS*0.50)]
    p75  = pnl_list[int(N_SIMS*0.75)]
    p95  = pnl_list[int(N_SIMS*0.95)]
    pos  = (N_SIMS - loss_runs) / N_SIMS
    mdd_p50 = dd_list[int(N_SIMS*0.50)]
    mdd_p95 = dd_list[int(N_SIMS*0.95)]

    all_results[scen_name] = {
        "p5":p5,"p50":p50,"p95":p95,
        "pos":pos,"mdd_p50":mdd_p50,"mdd_p95":mdd_p95,
        "loss_runs":loss_runs,
        "sharpe": _stats.mean(sharpe_list),
    }

    print(f"시나리오: {scen_name} ({s_desc})")
    print(f"  승률={s_wr*100:.1f}%  평균수익={s_aw*100:+.3f}%  평균손실={s_al*100:+.3f}%")
    print(f"\n  {DAYS}일 ({N_TRADES}건) 후 순손익 분포 (건당 {TRADE_AMOUNT:,.0f}원 고정):")
    print(f"  {'백분위':>6} | {'순손익(KRW)':>14} | {'비고':>10}")
    print(f"  {'-'*6}-+-{'-'*14}-+-{'-'*10}")
    for pct_nm, val in [("5%",p5),("25%",p25),("50%",p50),("75%",p75),("95%",p95)]:
        flag = "✅ 수익" if val>0 else "❌ 손실"
        print(f"  {pct_nm:>6} | {val:>+14,.0f}원 | {flag}")

    print(f"\n  수익 확률:        {pos*100:.1f}%")
    print(f"  손실 시뮬레이션:  {loss_runs}회 / {N_SIMS}회")
    print(f"  MDD 중앙값:       {mdd_p50:>10,.0f}원")
    print(f"  MDD 최악(95%):    {mdd_p95:>10,.0f}원")
    print(f"  평균 Sharpe:      {_stats.mean(sharpe_list):.4f}")

    # 일별 기대 순손익
    daily_exp = (s_wr*s_aw + (1-s_wr)*s_al) * TRADE_AMOUNT * TRADES_PER_DAY
    monthly_exp = daily_exp * DAYS
    print(f"\n  일별 기대 순손익: {daily_exp:>+10,.0f}원")
    print(f"  월간 기대 순손익: {monthly_exp:>+10,.0f}원")
    print(f"{'='*65}")

# ══════════════════════════════════════════════════════════
# VaR 분석 (KRW 절대금액)
# ══════════════════════════════════════════════════════════
print("VaR / CVaR 분석 (현재 시스템, KRW 절대금액)")
print(f"{'='*65}")
curr_pnl = sorted([0.0] * N_SIMS)  # 재계산
_pnl_curr = []
for _ in range(N_SIMS):
    pnl, _, _ = simulate_fixed(N_TRADES, wr, avg_w, avg_l,
                                vol_w, vol_l, TRADE_AMOUNT)
    _pnl_curr.append(pnl)
_pnl_curr.sort()

var_95  = _pnl_curr[int(N_SIMS*0.05)]
var_99  = _pnl_curr[int(N_SIMS*0.01)]
cvar_95 = _stats.mean(_pnl_curr[:int(N_SIMS*0.05)])
cvar_99 = _stats.mean(_pnl_curr[:int(N_SIMS*0.01)])

print(f"\n  {DAYS}일 ({N_TRADES}건) 기준:")
print(f"  VaR  95% : {var_95:>+12,.0f}원  ← 95% 확률로 이 금액 이상 수익")
print(f"  VaR  99% : {var_99:>+12,.0f}원  ← 99% 확률로 이 금액 이상 수익")
print(f"  CVaR 95% : {cvar_95:>+12,.0f}원  ← 최악 5% 평균 손익")
print(f"  CVaR 99% : {cvar_99:>+12,.0f}원  ← 최악 1% 평균 손익")

# ══════════════════════════════════════════════════════════
# 연속 손실 스트레스
# ══════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("연속 손실 스트레스 테스트")
print(f"{'='*65}")
streaks = []
for _ in range(N_SIMS):
    cur = mx = 0
    for _ in range(N_TRADES):
        if random.random() >= wr:
            cur += 1; mx = max(mx, cur)
        else:
            cur = 0
    streaks.append(mx)
streaks.sort()
p50s = streaks[int(N_SIMS*0.50)]
p95s = streaks[int(N_SIMS*0.95)]
p99s = streaks[int(N_SIMS*0.99)]

print(f"\n  {N_TRADES}건 중 최대 연속 손실:")
print(f"  중앙값 50%: {p50s}연속 → 손실 {TRADE_AMOUNT*abs(avg_l)*p50s:,.0f}원")
print(f"  최악   95%: {p95s}연속 → 손실 {TRADE_AMOUNT*abs(avg_l)*p95s:,.0f}원")
print(f"  극단   99%: {p99s}연속 → 손실 {TRADE_AMOUNT*abs(avg_l)*p99s:,.0f}원")

# ══════════════════════════════════════════════════════════
# 최종 평가
# ══════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("Monte Carlo v2 최종 종합 평가")
print(f"{'='*65}")

curr_r = all_results["현재 시스템"]
bear_r = all_results["보수적 -10%"]

checks = [
    ("수익 확률 > 80%",             curr_r["pos"] > 0.80),
    ("손실 시뮬레이션 < 5%",        curr_r["loss_runs"] < N_SIMS*0.05),
    ("최악 5% VaR 양수",            var_95 > 0),
    ("MDD 중앙값 < 50만원",         curr_r["mdd_p50"] < 500_000),
    ("MDD 최악 95% < 200만원",      curr_r["mdd_p95"] < 2_000_000),
    ("월간 기대수익 > 0원",
        (wr*avg_w + (1-wr)*avg_l)*TRADE_AMOUNT*N_TRADES > 0),
    ("보수적 수익확률 > 70%",       bear_r["pos"] > 0.70),
    ("연속손실 95% < 10회",         p95s < 10),
]

passed = sum(1 for _,v in checks if v)
print()
for name, result in checks:
    print(f"  {'✅' if result else '❌'} {name}")

score = passed/len(checks)*100
grade = ("S급 기관급" if score>=87.5 else
         "A급 우수"   if score>=75.0 else
         "B급 양호"   if score>=62.5 else "C급 개선필요")
print(f"\n  {passed}/{len(checks)} 기준 충족")
print(f"  점수: {score:.0f}/100  등급: {grade}")
print(f"\n{'='*65}")
print(f"완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
