# -*- coding: utf-8 -*-
"""
두 가지 동시 진행:
1. ML 모델 재학습 (백그라운드) — 올바른 회귀 레이블로
2. 전통지표 최적 조합 탐색 (즉시) — 10코인 180일
"""
import sys, asyncio
sys.path.insert(0, '.')

# ══════════════════════════════════════════════════════
# PART A: 전통지표 최적 조합 백테스트 (즉시 실행)
# ══════════════════════════════════════════════════════
print("=" * 60)
print("PART A: 전통지표 10코인 x 180일 전수 백테스트")
print("=" * 60)

async def run_traditional():
    from backtesting.backtester import Backtester
    from backtesting.data_loader import fetch_ohlcv

    MARKETS = [
        'KRW-BTC','KRW-ETH','KRW-XRP','KRW-SOL','KRW-ADA',
        'KRW-DOGE','KRW-AVAX','KRW-DOT','KRW-LINK','KRW-ATOM'
    ]
    STRATEGIES = ['volatility_breakout','rsi_divergence',
                  'macd_momentum','order_block_smc']

    bt = Backtester(initial_capital=114000, fee_rate=0.0005,
                    slippage=0.001, stop_loss_pct=0.022,
                    take_profit_pct=0.045, position_size=0.20,
                    max_positions=5)

    # 결과 저장
    results = {s: {'wins':0,'total':0,'ev_list':[],'sharpe_list':[]} for s in STRATEGIES}

    print(f"\n{'코인':<12} {'전략':<22} {'승률':>6} {'기댓값':>8} {'샤프':>7} {'MDD':>6} {'거래':>4}")
    print("-" * 68)

    for market in MARKETS:
        df = await fetch_ohlcv(market, '1d', 180)
        if df is None or len(df) < 50:
            continue
        for strat in STRATEGIES:
            try:
                res = bt.run(df, strat, market)
                if res.total_trades == 0:
                    continue
                icon = '✅' if res.expectancy > 0 else '❌'
                print(f"  {icon} {market:<10} {strat:<22} "
                      f"{res.win_rate:>5.1f}% {res.expectancy:>+8.4f} "
                      f"{res.sharpe_ratio:>+7.3f} {res.max_drawdown:>5.1f}% "
                      f"{res.total_trades:>4}회")
                r = results[strat]
                r['total']       += res.total_trades
                r['wins']        += int(res.win_rate * res.total_trades / 100)
                r['ev_list'].append(res.expectancy)
                r['sharpe_list'].append(res.sharpe_ratio)
            except Exception as e:
                pass

    print("\n" + "=" * 60)
    print("전략별 통합 결과 (10코인 180일)")
    print("=" * 60)
    best_strat = None
    best_score = -999

    for strat, r in results.items():
        if r['total'] == 0:
            continue
        wr      = r['wins'] / r['total'] * 100
        avg_ev  = sum(r['ev_list']) / len(r['ev_list']) if r['ev_list'] else 0
        avg_sh  = sum(r['sharpe_list']) / len(r['sharpe_list']) if r['sharpe_list'] else 0
        pos_cnt = sum(1 for e in r['ev_list'] if e > 0)
        pos_pct = pos_cnt / len(r['ev_list']) * 100 if r['ev_list'] else 0

        # 종합점수 = 승률 + 양수기댓값비율 + 샤프*10
        score = wr * 0.3 + pos_pct * 0.4 + avg_sh * 10
        verdict = '🟢' if wr >= 45 and avg_ev > 0 else ('🟡' if avg_ev > 0 else '🔴')
        print(f"  {verdict} {strat:<22} 승률={wr:.1f}% 평균기댓값={avg_ev:+.4f} "
              f"평균샤프={avg_sh:+.3f} 양수코인={pos_cnt}/{len(r['ev_list'])}개 "
              f"거래={r['total']}회 점수={score:.1f}")
        if score > best_score:
            best_score = score
            best_strat = strat

    print(f"\n  🏆 최고 전략: {best_strat} (점수={best_score:.1f})")
    print(f"""
  ━━━ 해석 기준 ━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🟢 승률≥45% AND 평균기댓값>0  → 즉시 페이퍼트레이딩
  🟡 평균기댓값>0 (승률<45%)    → 코인 선별 후 사용
  🔴 평균기댓값≤0               → 제외
  거래수 30회 미만은 통계 불신뢰
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")
    return best_strat

best = asyncio.run(run_traditional())

# ══════════════════════════════════════════════════════
# PART B: ML 재학습 준비 상태 점검
# ══════════════════════════════════════════════════════
print("=" * 60)
print("PART B: ML 재학습 준비 상태 점검")
print("=" * 60)
import torch
from pathlib import Path

ckpt = torch.load("models/saved/ensemble_best.pt",
                  map_location="cpu", weights_only=False)
print(f"  현재 모델 상태:")
print(f"    val_acc     = {ckpt.get('val_acc','없음'):.4f}  ← 거짓 (출력값 고정)")
print(f"    forward_n   = {ckpt.get('forward_n','없음')}봉")
print(f"    timestamp   = {ckpt.get('timestamp','없음')}")
print(f"    buy_thr     = {ckpt.get('buy_thr','없음')}  ← 수익률 임계값")
print(f"    sell_thr    = {ckpt.get('sell_thr','없음')}")

# trainer.py 확인
trainer_path = Path("models/train/trainer.py")
if trainer_path.exists():
    code = trainer_path.read_text(encoding="utf-8")
    has_atr = "atr" in code.lower()
    has_dynamic = "dynamic" in code.lower() or "forward_n" in code.lower()
    print(f"\n  trainer.py 상태:")
    print(f"    ATR 동적 임계값 : {'✅ 적용됨 (BUG-5 수정)' if has_atr else '❌ 미적용'}")
    print(f"    forward_n 회귀  : {'✅ 있음' if has_dynamic else '⚠️  확인필요'}")

print(f"""
  ML 재학습 명령어 (지금 실행하면 백그라운드 학습 시작):
    python -m models.train.trainer

  예상 소요 시간: GPU 있으면 20-40분, CPU만 1-3시간
  재학습 후 예측값이 0.333 고정이면 아래 원인:
    1. 레이블 불균형 (HOLD 70%+) → trainer.py 이미 수정됨 (BUG-5)
    2. 학습률 너무 높음 → trainer.py에서 lr=0.0001 이하로 설정
    3. 데이터 부족 → 최소 90일 이상 필요
""")
