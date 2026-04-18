#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
APEX BOT Monte Carlo 시뮬레이션
실거래 데이터 기반 1,000회 시뮬레이션 – 전략 robustness 검증
"""
import sys, os, random, statistics as _stats, math
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

random.seed(None)  # 매 실행마다 다른 시드

print(f"\nAPEX BOT Monte Carlo 시뮬레이션")
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
    print(f"[DB] 실거래 {len(_trades)}건 로드 (4/15 이후)")
except Exception as e:
    print(f"[DB FAIL] {e}")

# ── 실거래 통계 ───────────────────────────────────────────
rates     = [r[0] / 100 for r in _trades]   # % → 소수
FEE_COST  = 0.0026   # 왕복 비용 0.26%
net_rates = [r - FEE_COST for r in rates]   # 실질 손익

wins  = [r for r in net_rates if r > 0]
loss  = [r for r in net_rates if r <= 0]
n_total = len(net_rates)
wr    = len(wins) / n_total if n_total else 0
avg_w = _stats.mean(wins)  if wins else 0
avg_l = _stats.mean(loss)  if loss else 0
pf    = sum(wins) / abs(sum(loss)) if loss else 999

print(f"\n[실측 기준값 (비용 차감 후)]")
print(f"  거래수:    {n_total}건")
print(f"  승률:      {wr*100:.1f}%")
print(f"  평균수익:  {avg_w*100:+.3f}%")
print(f"  평균손실:  {avg_l*100:+.3f}%")
print(f"  Profit Factor: {pf:.3f}")
print(f"  왕복비용:  {FEE_COST*100:.3f}%")

# ══════════════════════════════════════════════════════════
# Monte Carlo 엔진
# ══════════════════════════════════════════════════════════
N_SIMS       = 1000    # 시뮬레이션 횟수
TRADES_PER_SIM = 252   # 1년 거래 수 (하루 20건 × 252 거래일 아님 → 실측 기반)
# 실측: 4일간 80건 → 하루 20건 → 1개월 440건 → 연간 5,280건
# 보수적으로 연간 2,000건 사용
TRADES_PER_SIM = 500   # 약 25일치 (보수적)
INITIAL_CAPITAL = 1_000_000  # 100만원 기준

def simulate_once(n_trades, wr, avg_win, avg_loss, vol_win=None, vol_loss=None):
    """단일 시뮬레이션: 거래별 손익 누적"""
    equity = INITIAL_CAPITAL
    peak   = equity
    mdd    = 0.0
    pnl_list = []

    _vol_w = vol_win  or abs(avg_win)  * 0.5
    _vol_l = vol_loss or abs(avg_loss) * 0.5

    for _ in range(n_trades):
        if random.random() < wr:
            pnl_pct = random.gauss(avg_win,  _vol_w)
            pnl_pct = max(pnl_pct, 0.0001)   # 수익 거래는 양수 보장
        else:
            pnl_pct = random.gauss(avg_loss, _vol_l)
            pnl_pct = min(pnl_pct, -0.001)   # 손실 거래는 음수 보장
            pnl_pct = max(pnl_pct, -0.025)   # 최대 손실 -2.5% 클램프

        equity  *= (1 + pnl_pct)
        pnl_list.append(pnl_pct)

        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak
        if dd > mdd:
            mdd = dd

    total_return = (equity - INITIAL_CAPITAL) / INITIAL_CAPITAL
    sharpe_raw   = (_stats.mean(pnl_list) / _stats.stdev(pnl_list)
                    if len(pnl_list) > 1 and _stats.stdev(pnl_list) > 0 else 0)
    return total_return, mdd, sharpe_raw, equity

# ── 실측 변동성 계산 ──────────────────────────────────────
vol_win  = _stats.stdev(wins)  if len(wins)  > 1 else abs(avg_w) * 0.5
vol_loss = _stats.stdev(loss)  if len(loss)  > 1 else abs(avg_l) * 0.5

print(f"\n  수익 변동성: {vol_win*100:.3f}%")
print(f"  손실 변동성: {vol_loss*100:.3f}%")
print(f"\n[Monte Carlo 설정]")
print(f"  시뮬레이션: {N_SIMS:,}회")
print(f"  거래수/회:  {TRADES_PER_SIM}건")
print(f"  초기자본:   {INITIAL_CAPITAL:,}원")

# ══════════════════════════════════════════════════════════
# 3가지 시나리오 시뮬레이션
# ══════════════════════════════════════════════════════════
scenarios = [
    # (이름, 승률, 평균수익, 평균손실, 설명)
    ("현재 시스템",    wr,      avg_w,      avg_l,      "실측 그대로"),
    ("보수적 (-10%)",  wr*0.90, avg_w*0.90, avg_l*1.10, "승률/수익 10% 감소"),
    ("낙관적 (+10%)",  wr*1.05, avg_w*1.10, avg_l*0.90, "승률/수익 10% 증가"),
]
# 승률 상한 보정
scenarios = [(n, min(w, 0.95), aw, al, d) for n,w,aw,al,d in scenarios]

all_results = {}

for scen_name, s_wr, s_aw, s_al, s_desc in scenarios:
    returns = []
    mdds    = []
    sharpes = []
    ruins   = 0   # MDD > 50% (사실상 파산)

    for _ in range(N_SIMS):
        ret, mdd, sharpe, _ = simulate_once(
            TRADES_PER_SIM, s_wr, s_aw, s_al, vol_win, vol_loss
        )
        returns.append(ret)
        mdds.append(mdd)
        sharpes.append(sharpe)
        if mdd > 0.50:
            ruins += 1

    returns.sort()
    mdds.sort()

    # 백분위 계산
    p5   = returns[int(N_SIMS * 0.05)]
    p25  = returns[int(N_SIMS * 0.25)]
    p50  = returns[int(N_SIMS * 0.50)]
    p75  = returns[int(N_SIMS * 0.75)]
    p95  = returns[int(N_SIMS * 0.95)]
    pos_prob = sum(1 for r in returns if r > 0) / N_SIMS

    mdd_p50  = mdds[int(N_SIMS * 0.50)]
    mdd_p95  = mdds[int(N_SIMS * 0.95)]

    all_results[scen_name] = {
        "returns": returns, "mdds": mdds, "sharpes": sharpes,
        "p5": p5, "p25": p25, "p50": p50, "p75": p75, "p95": p95,
        "pos_prob": pos_prob, "ruin_rate": ruins/N_SIMS,
        "mdd_p50": mdd_p50, "mdd_p95": mdd_p95,
        "wr": s_wr, "desc": s_desc,
    }

    print(f"\n{'='*65}")
    print(f"시나리오: {scen_name} ({s_desc})")
    print(f"  승률={s_wr*100:.1f}%  평균수익={s_aw*100:+.3f}%  평균손실={s_al*100:+.3f}%")
    print(f"  거래 {TRADES_PER_SIM}건 후 수익률 분포:")
    print(f"  {'백분위':>8} | {'수익률':>10} | {'100만원 기준':>14}")
    print(f"  {'-'*8}-+-{'-'*10}-+-{'-'*14}")
    for pct, val in [("5%", p5),("25%",p25),("50%",p50),("75%",p75),("95%",p95)]:
        krw = INITIAL_CAPITAL * (1 + val)
        print(f"  {pct:>8} | {val*100:>+9.2f}% | {krw:>13,.0f}원")
    print(f"\n  수익 확률:    {pos_prob*100:.1f}%")
    print(f"  파산 확률:    {ruins/N_SIMS*100:.2f}% (MDD>50%)")
    print(f"  중앙값 MDD:   {mdd_p50*100:.2f}%")
    print(f"  최악 MDD(95): {mdd_p95*100:.2f}%")
    print(f"  평균 Sharpe:  {_stats.mean(sharpes):.4f}")

# ══════════════════════════════════════════════════════════
# 핵심 리스크 지표
# ══════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("Monte Carlo 핵심 리스크 지표 요약")
print(f"{'='*65}")
print(f"  {'시나리오':<16} | {'수익확률':>6} | {'중앙수익':>8} | {'중앙MDD':>8} | {'파산확률':>8}")
print(f"  {'-'*16}-+-{'-'*6}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}")
for scen_name, r in all_results.items():
    icon = "✅" if r["pos_prob"] > 0.7 and r["ruin_rate"] < 0.01 else "⚠️"
    print(f"  {icon} {scen_name:<14} | {r['pos_prob']*100:>5.1f}% | "
          f"{r['p50']*100:>+7.2f}% | {r['mdd_p50']*100:>7.2f}% | "
          f"{r['ruin_rate']*100:>7.3f}%")

# ══════════════════════════════════════════════════════════
# Value at Risk (VaR) 및 CVaR
# ══════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("VaR / CVaR 분석 (현재 시스템 기준)")
print(f"{'='*65}")
curr = all_results["현재 시스템"]
returns_sorted = sorted(curr["returns"])

var_95  = returns_sorted[int(N_SIMS * 0.05)]   # 5% VaR
var_99  = returns_sorted[int(N_SIMS * 0.01)]   # 1% VaR
cvar_95 = _stats.mean(returns_sorted[:int(N_SIMS * 0.05)])  # CVaR 95%
cvar_99 = _stats.mean(returns_sorted[:int(N_SIMS * 0.01)])  # CVaR 99%

print(f"\n  {TRADES_PER_SIM}건 거래 후 (초기자본 {INITIAL_CAPITAL:,}원 기준):")
print(f"  VaR  95% : {var_95*100:>+7.2f}%  ({INITIAL_CAPITAL*(1+var_95):>12,.0f}원)")
print(f"  VaR  99% : {var_99*100:>+7.2f}%  ({INITIAL_CAPITAL*(1+var_99):>12,.0f}원)")
print(f"  CVaR 95% : {cvar_95*100:>+7.2f}%  ({INITIAL_CAPITAL*(1+cvar_95):>12,.0f}원)")
print(f"  CVaR 99% : {cvar_99*100:>+7.2f}%  ({INITIAL_CAPITAL*(1+cvar_99):>12,.0f}원)")
print(f"\n  해석: 최악 5% 시나리오에서도 {var_95*100:+.1f}% 수익/손실")

# ══════════════════════════════════════════════════════════
# Kelly 기준 최적 자본 배분
# ══════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("Kelly 기준 최적 자본 배분 분석")
print(f"{'='*65}")

def full_kelly(wr, avg_win, avg_loss):
    if avg_loss == 0: return 0
    b = abs(avg_win / avg_loss)
    return max(0, (wr * b - (1-wr)) / b)

for scen_name, s_wr, s_aw, s_al, s_desc in scenarios:
    fk  = full_kelly(s_wr, abs(s_aw), abs(s_al))
    hk  = fk * 0.5
    qk  = fk * 0.25
    cap = min(hk, 0.20)
    print(f"\n  [{scen_name}]")
    print(f"  Full Kelly:    {fk*100:.2f}%")
    print(f"  Half Kelly:    {hk*100:.2f}%  ← 권장")
    print(f"  Quarter Kelly: {qk*100:.2f}%  ← 보수적")
    print(f"  적용 상한:     {cap*100:.2f}%  (20% 캡)")

# ══════════════════════════════════════════════════════════
# 연속 손실 스트레스 테스트
# ══════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("연속 손실 스트레스 테스트")
print(f"{'='*65}")

max_consec_losses = []
for _ in range(N_SIMS):
    cur_streak = max_streak = 0
    for _ in range(TRADES_PER_SIM):
        if random.random() >= wr:
            cur_streak += 1
            max_streak = max(max_streak, cur_streak)
        else:
            cur_streak = 0
    max_consec_losses.append(max_streak)

max_consec_losses.sort()
p50_streak = max_consec_losses[int(N_SIMS * 0.50)]
p95_streak = max_consec_losses[int(N_SIMS * 0.95)]
p99_streak = max_consec_losses[int(N_SIMS * 0.99)]

print(f"\n  {TRADES_PER_SIM}건 거래 중 최대 연속 손실:")
print(f"  중앙값 (50%): {p50_streak}연속")
print(f"  최악  (95%): {p95_streak}연속")
print(f"  극단  (99%): {p99_streak}연속")

# 연속 손실 후 자본 손실률
for streak in [p50_streak, p95_streak, p99_streak]:
    capital_loss = 1 - (1 + avg_l) ** streak
    print(f"  {streak}연속 손실 시 자본 손실: {capital_loss*100:.2f}%  "
          f"({'✅ 허용' if capital_loss < 0.15 else '⚠️ 주의' if capital_loss < 0.30 else '❌ 위험'})")

# ══════════════════════════════════════════════════════════
# 최종 종합 평가
# ══════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("Monte Carlo 최종 종합 평가")
print(f"{'='*65}")

curr_r = all_results["현재 시스템"]
bear_r = all_results["보수적 (-10%)"]

checks = [
    ("수익 확률 > 70%",      curr_r["pos_prob"] > 0.70),
    ("파산 확률 < 1%",       curr_r["ruin_rate"] < 0.01),
    ("중앙값 MDD < 15%",     curr_r["mdd_p50"]  < 0.15),
    ("최악 MDD < 30%",       curr_r["mdd_p95"]  < 0.30),
    ("중앙값 수익 > 0%",     curr_r["p50"] > 0),
    ("보수적 수익확률 > 60%", bear_r["pos_prob"] > 0.60),
    ("VaR 95% > -20%",       var_95 > -0.20),
    (f"연속손실 95% < 10연속", p95_streak < 10),
]

passed = sum(1 for _, v in checks if v)
print()
for name, result in checks:
    icon = "✅" if result else "❌"
    print(f"  {icon} {name}")

print(f"\n  {passed}/{len(checks)} 기준 충족")
score = passed / len(checks) * 100
grade = ("S급 기관급" if score >= 87.5 else
         "A급 우수"   if score >= 75.0 else
         "B급 양호"   if score >= 62.5 else "C급 개선필요")
print(f"  Monte Carlo 점수: {score:.0f}/100  등급: {grade}")
print(f"\n{'='*65}")
print(f"완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
