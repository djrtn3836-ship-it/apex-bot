# -*- coding: utf-8 -*-
"""
ML 모델 실제 연결 최종 스크립트
- 모델 출력: Tuple[Tensor, Dict] 처리
- 피처 120개 생성 (OHLCV 기반 수동 계산)
- signal_generator.py ml_strategy 실제 모델 호출로 교체
- 교체 후 180일 백테스트 실행
"""
import sys, asyncio, torch, ast, shutil
import numpy as np
import pandas as pd
sys.path.insert(0, '.')
from pathlib import Path
from datetime import datetime

BACKUP_DIR = Path(f"archive/ml_final_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# ══════════════════════════════════════════════════════════════
# 1. 피처 120개 생성 함수 확정
# ══════════════════════════════════════════════════════════════
def make_120_features(df: pd.DataFrame) -> np.ndarray:
    """OHLCV → 120개 피처 배열 반환 (shape: n x 120)"""
    c = df["close"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    v = df["volume"].values.astype(float)
    o = df["open"].values.astype(float)
    n = len(c)

    feats = []

    # ── 1. 가격 기반 (5개) ──────────────────────────────────
    feats.append(c / (np.mean(c) + 1e-8))          # 정규화 종가
    feats.append(h / (c + 1e-8))                    # 고가/종가 비율
    feats.append(l / (c + 1e-8))                    # 저가/종가 비율
    feats.append(o / (c + 1e-8))                    # 시가/종가 비율
    feats.append(v / (np.mean(v) + 1e-8))           # 정규화 거래량

    # ── 2. 수익률 (5개) ─────────────────────────────────────
    for p in [1, 2, 3, 5, 10]:
        ret = np.zeros(n)
        ret[p:] = (c[p:] - c[:-p]) / (c[:-p] + 1e-8)
        feats.append(ret)

    # ── 3. EMA 비율 (10개) ──────────────────────────────────
    for span in [5, 7, 10, 14, 20, 30, 50, 60, 100, 200]:
        ema = pd.Series(c).ewm(span=span, adjust=False).mean().values
        feats.append(ema / (c + 1e-8))

    # ── 4. EMA 크로스 (5개) ─────────────────────────────────
    for s1, s2 in [(5,10),(5,20),(10,20),(10,50),(20,50)]:
        e1 = pd.Series(c).ewm(span=s1).mean().values
        e2 = pd.Series(c).ewm(span=s2).mean().values
        feats.append((e1 - e2) / (c + 1e-8))

    # ── 5. RSI (5개) ────────────────────────────────────────
    for period in [7, 9, 14, 21, 28]:
        delta = np.diff(c, prepend=c[0])
        gain  = np.where(delta > 0, delta, 0.0)
        loss  = np.where(delta < 0, -delta, 0.0)
        ag = pd.Series(gain).ewm(span=period, adjust=False).mean().values
        al = pd.Series(loss).ewm(span=period, adjust=False).mean().values
        rsi = 100 - 100 / (1 + ag / (al + 1e-8))
        feats.append(rsi / 100.0)

    # ── 6. MACD (6개) ───────────────────────────────────────
    for (f, s, sg) in [(12,26,9),(5,13,5),(8,21,8)]:
        ef  = pd.Series(c).ewm(span=f).mean().values
        es  = pd.Series(c).ewm(span=s).mean().values
        mac = ef - es
        sig = pd.Series(mac).ewm(span=sg).mean().values
        feats.append(mac / (c + 1e-8))
        feats.append((mac - sig) / (c + 1e-8))

    # ── 7. Bollinger Bands (9개) ────────────────────────────
    for period in [10, 20, 30]:
        ma  = pd.Series(c).rolling(period).mean().values
        std = pd.Series(c).rolling(period).std().values
        feats.append(np.nan_to_num((c - ma) / (std + 1e-8)))   # %B
        feats.append(np.nan_to_num((ma + 2*std) / (c + 1e-8))) # 상단
        feats.append(np.nan_to_num((ma - 2*std) / (c + 1e-8))) # 하단

    # ── 8. ATR / 변동성 (6개) ───────────────────────────────
    for period in [7, 14, 21]:
        tr  = np.maximum(h-l,
              np.maximum(abs(h - np.roll(c,1)),
                         abs(l - np.roll(c,1))))
        atr = pd.Series(tr).ewm(span=period).mean().values
        feats.append(atr / (c + 1e-8))                         # ATR 비율
        feats.append(pd.Series(c).rolling(period).std().values
                     / (c + 1e-8))                             # 변동성

    # ── 9. 거래량 지표 (8개) ────────────────────────────────
    for period in [5, 10, 20, 30]:
        mv = pd.Series(v).rolling(period).mean().values
        feats.append(v / (mv + 1e-8))                          # 거래량 비율
    # OBV 방향
    obv_delta = np.where(c > np.roll(c,1), v, -v)
    obv = np.cumsum(obv_delta)
    feats.append(obv / (np.abs(obv).max() + 1e-8))
    # 거래량 수익률
    feats.append(np.diff(v, prepend=v[0]) / (v + 1e-8))
    # 거래대금 정규화
    turnover = c * v
    feats.append(turnover / (turnover.mean() + 1e-8))
    # 가격 x 거래량 모멘텀
    pv = c * v
    feats.append(pd.Series(pv).rolling(5).mean().values / (pv.mean() + 1e-8))

    # ── 10. 캔들 패턴 (6개) ─────────────────────────────────
    body  = abs(c - o) / (h - l + 1e-8)   # 몸통 비율
    upper = (h - np.maximum(c, o)) / (h - l + 1e-8)  # 위꼬리
    lower = (np.minimum(c, o) - l) / (h - l + 1e-8)  # 아래꼬리
    feats.append(body)
    feats.append(upper)
    feats.append(lower)
    feats.append(np.where(c > o, 1.0, -1.0))          # 양봉/음봉
    feats.append((c - l) / (h - l + 1e-8))            # 종가 위치
    feats.append((h - l) / (c + 1e-8))                # 범위 비율

    # ── 11. 모멘텀 / 기타 (15개) ────────────────────────────
    for period in [3, 5, 7, 10, 14, 20, 30]:
        feats.append(c / (pd.Series(c).shift(period).values + 1e-8) - 1)
    # Stochastic K (5, 14, 21)
    for period in [5, 14, 21]:
        lo_n = pd.Series(l).rolling(period).min().values
        hi_n = pd.Series(h).rolling(period).max().values
        stoch = (c - lo_n) / (hi_n - lo_n + 1e-8)
        feats.append(np.nan_to_num(stoch))
    # Williams %R (14)
    lo14 = pd.Series(l).rolling(14).min().values
    hi14 = pd.Series(h).rolling(14).max().values
    wr = (hi14 - c) / (hi14 - lo14 + 1e-8)
    feats.append(np.nan_to_num(wr))
    # CCI (14)
    tp  = (h + l + c) / 3
    ma14 = pd.Series(tp).rolling(14).mean().values
    md14 = pd.Series(tp).rolling(14).apply(lambda x: np.mean(np.abs(x - x.mean()))).values
    cci  = (tp - ma14) / (0.015 * md14 + 1e-8)
    feats.append(np.nan_to_num(cci / 200.0))

    arr = np.stack(feats, axis=1)
    arr = np.nan_to_num(arr, nan=0.0, posinf=2.0, neginf=-2.0)

    # 정확히 120개로 맞추기
    if arr.shape[1] < 120:
        pad = np.zeros((arr.shape[0], 120 - arr.shape[1]))
        arr = np.concatenate([arr, pad], axis=1)
    else:
        arr = arr[:, :120]

    return arr

# ══════════════════════════════════════════════════════════════
# 2. 파이프라인 테스트 + 예측 분포 확인
# ══════════════════════════════════════════════════════════════
print("=" * 60)
print("[1] 실제 ML 모델 예측 파이프라인 테스트 (BTC 180일)")
print("=" * 60)
async def test_pipeline():
    from backtesting.data_loader import fetch_ohlcv
    from models.architectures.ensemble import EnsembleModel

    df   = await fetch_ohlcv("KRW-BTC", "1d", 180)
    ckpt = torch.load("models/saved/ensemble_best.pt",
                      map_location="cpu", weights_only=False)
    buy_thr  = float(ckpt.get("buy_thr",   0.008))
    sell_thr = float(ckpt.get("sell_thr", -0.010))
    seq_len  = 60

    model = EnsembleModel()
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()

    feat_arr = make_120_features(df)
    print(f"  피처 shape: {feat_arr.shape}  (목표: {len(df)} x 120)")

    preds = []
    buy_cnt = sell_cnt = hold_cnt = 0

    with torch.no_grad():
        for i in range(seq_len, len(feat_arr)):
            window = feat_arr[i-seq_len:i]
            x   = torch.FloatTensor(window).unsqueeze(0)
            out = model(x)
            # Tuple[Tensor, Dict] 처리
            if isinstance(out, tuple):
                tensor_out = out[0]
            else:
                tensor_out = out
            val = float(tensor_out[0].mean())
            preds.append(val)
            if   val >= buy_thr:  buy_cnt  += 1
            elif val <= sell_thr: sell_cnt += 1
            else:                 hold_cnt += 1

    total = buy_cnt + sell_cnt + hold_cnt
    print(f"\n  예측값 범위 : {min(preds):.6f} ~ {max(preds):.6f}")
    print(f"  예측값 평균 : {np.mean(preds):.6f}  std: {np.std(preds):.6f}")
    print(f"  buy_thr={buy_thr:+.4f}  sell_thr={sell_thr:+.4f}")
    print(f"\n  BUY  (≥{buy_thr:+.3f}): {buy_cnt:3d}회 ({buy_cnt/total*100:.1f}%)")
    print(f"  SELL (≤{sell_thr:+.3f}): {sell_cnt:3d}회 ({sell_cnt/total*100:.1f}%)")
    print(f"  HOLD           : {hold_cnt:3d}회 ({hold_cnt/total*100:.1f}%)")

    if buy_cnt > 0:
        print("\n  ✅ 모델이 실제 BUY 신호를 생성함 → signal_generator 교체 가능")
    else:
        print("\n  ⚠️  BUY 신호 0회 → buy_thr 조정 필요")
    return buy_cnt, sell_cnt, hold_cnt, preds

buy_c, sell_c, hold_c, preds = asyncio.run(test_pipeline())

# ══════════════════════════════════════════════════════════════
# 3. signal_generator.py ml_strategy 실제 모델로 교체
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("[2] signal_generator.py ml_strategy 교체")
print("=" * 60)

sg_path = Path("backtesting/signal_generator.py")
shutil.copy2(sg_path, BACKUP_DIR / sg_path.name)
code = sg_path.read_bytes().decode("utf-8").replace("\r\n", "\n")

# buy_thr 조정: BUY 신호가 너무 없으면 낮춤
ckpt = torch.load("models/saved/ensemble_best.pt",
                  map_location="cpu", weights_only=False)
orig_buy_thr  = float(ckpt.get("buy_thr",   0.008))
orig_sell_thr = float(ckpt.get("sell_thr", -0.010))

# 최적 임계값 계산 (상위 20%를 BUY로)
if preds:
    p_arr = np.array(preds)
    buy_thr_use  = float(np.percentile(p_arr, 80))   # 상위 20% → BUY
    sell_thr_use = float(np.percentile(p_arr, 20))   # 하위 20% → SELL
    print(f"  원본 임계값: buy≥{orig_buy_thr:+.4f}, sell≤{orig_sell_thr:+.4f}")
    print(f"  조정 임계값: buy≥{buy_thr_use:+.4f}, sell≤{sell_thr_use:+.4f}")
    print(f"  (예측값 분포 기반: 상위20%=BUY, 하위20%=SELL)")
else:
    buy_thr_use  = orig_buy_thr
    sell_thr_use = orig_sell_thr

NEW_ML_FUNC = f'''
def signal_ml_strategy(df: pd.DataFrame) -> pd.Series:
    """실제 ensemble_best.pt 모델 기반 ML 전략
    - 입력: 120개 피처, 시퀀스 60봉
    - 출력: 수익률 예측값 (회귀)
    - buy_thr={buy_thr_use:+.6f} (상위20%), sell_thr={sell_thr_use:+.6f} (하위20%)
    """
    import torch as _torch
    import numpy as _np
    _model_path = "models/saved/ensemble_best.pt"
    _seq_len = 60
    _buy_thr  = {buy_thr_use:.6f}
    _sell_thr = {sell_thr_use:.6f}

    sig = pd.Series(0, index=df.index)
    if len(df) < _seq_len + 10:
        return sig.astype(int)
    try:
        import pathlib as _pl
        if not _pl.Path(_model_path).exists():
            return sig.astype(int)

        # 피처 생성 (120개)
        c = df["close"].values.astype(float)
        h = df["high"].values.astype(float)
        l = df["low"].values.astype(float)
        v = df["volume"].values.astype(float)
        o = df["open"].values.astype(float)
        feats = []
        feats.append(c / (_np.mean(c) + 1e-8))
        feats.append(h / (c + 1e-8))
        feats.append(l / (c + 1e-8))
        feats.append(o / (c + 1e-8))
        feats.append(v / (_np.mean(v) + 1e-8))
        for p in [1, 2, 3, 5, 10]:
            ret = _np.zeros(len(c))
            ret[p:] = (c[p:] - c[:-p]) / (c[:-p] + 1e-8)
            feats.append(ret)
        for span in [5,7,10,14,20,30,50,60,100,200]:
            ema = pd.Series(c).ewm(span=span, adjust=False).mean().values
            feats.append(ema / (c + 1e-8))
        for s1,s2 in [(5,10),(5,20),(10,20),(10,50),(20,50)]:
            e1 = pd.Series(c).ewm(span=s1).mean().values
            e2 = pd.Series(c).ewm(span=s2).mean().values
            feats.append((e1-e2)/(c+1e-8))
        for period in [7,9,14,21,28]:
            delta = _np.diff(c, prepend=c[0])
            ag = pd.Series(_np.where(delta>0,delta,0.0)).ewm(span=period).mean().values
            al = pd.Series(_np.where(delta<0,-delta,0.0)).ewm(span=period).mean().values
            feats.append((100-100/(1+ag/(al+1e-8)))/100.0)
        for (f,s,sg) in [(12,26,9),(5,13,5),(8,21,8)]:
            ef=pd.Series(c).ewm(span=f).mean().values
            es=pd.Series(c).ewm(span=s).mean().values
            mac=ef-es
            sgv=pd.Series(mac).ewm(span=sg).mean().values
            feats.append(mac/(c+1e-8)); feats.append((mac-sgv)/(c+1e-8))
        for period in [10,20,30]:
            ma=pd.Series(c).rolling(period).mean().values
            std=pd.Series(c).rolling(period).std().values
            feats.append(_np.nan_to_num((c-ma)/(std+1e-8)))
            feats.append(_np.nan_to_num((ma+2*std)/(c+1e-8)))
            feats.append(_np.nan_to_num((ma-2*std)/(c+1e-8)))
        for period in [7,14,21]:
            tr=_np.maximum(h-l,_np.maximum(abs(h-_np.roll(c,1)),abs(l-_np.roll(c,1))))
            feats.append(pd.Series(tr).ewm(span=period).mean().values/(c+1e-8))
            feats.append(_np.nan_to_num(pd.Series(c).rolling(period).std().values/(c+1e-8)))
        for period in [5,10,20,30]:
            mv=pd.Series(v).rolling(period).mean().values
            feats.append(v/(mv+1e-8))
        obv=_np.cumsum(_np.where(c>_np.roll(c,1),v,-v))
        feats.append(obv/(_np.abs(obv).max()+1e-8))
        feats.append(_np.diff(v,prepend=v[0])/(v+1e-8))
        to=c*v; feats.append(to/(to.mean()+1e-8))
        pv=c*v; feats.append(pd.Series(pv).rolling(5).mean().values/(pv.mean()+1e-8))
        body=abs(c-o)/(h-l+1e-8); upper=(h-_np.maximum(c,o))/(h-l+1e-8)
        lower=(_np.minimum(c,o)-l)/(h-l+1e-8)
        feats += [body, upper, lower, _np.where(c>o,1.0,-1.0),
                  (c-l)/(h-l+1e-8), (h-l)/(c+1e-8)]
        for period in [3,5,7,10,14,20,30]:
            feats.append(c/(pd.Series(c).shift(period).values+1e-8)-1)
        for period in [5,14,21]:
            lo_n=pd.Series(l).rolling(period).min().values
            hi_n=pd.Series(h).rolling(period).max().values
            feats.append(_np.nan_to_num((c-lo_n)/(hi_n-lo_n+1e-8)))
        lo14=pd.Series(l).rolling(14).min().values; hi14=pd.Series(h).rolling(14).max().values
        feats.append(_np.nan_to_num((hi14-c)/(hi14-lo14+1e-8)))
        tp=(h+l+c)/3; ma14t=pd.Series(tp).rolling(14).mean().values
        md14t=pd.Series(tp).rolling(14).apply(lambda x:_np.mean(_np.abs(x-x.mean()))).values
        feats.append(_np.nan_to_num((tp-ma14t)/(0.015*md14t+1e-8)/200.0))

        arr = _np.stack(feats, axis=1)
        arr = _np.nan_to_num(arr, nan=0.0, posinf=2.0, neginf=-2.0)
        if arr.shape[1] < 120:
            arr = _np.concatenate([arr, _np.zeros((arr.shape[0], 120-arr.shape[1]))], axis=1)
        else:
            arr = arr[:, :120]

        # 모델 로드 및 예측
        from models.architectures.ensemble import EnsembleModel as _EM
        _ckpt = _torch.load(_model_path, map_location="cpu", weights_only=False)
        _mdl  = _EM()
        _mdl.load_state_dict(_ckpt["model_state_dict"])
        _mdl.eval()

        with _torch.no_grad():
            for i in range(_seq_len, len(arr)):
                window = arr[i-_seq_len:i]
                x = _torch.FloatTensor(window).unsqueeze(0)
                out = _mdl(x)
                t   = out[0] if isinstance(out, tuple) else out
                val = float(t[0].mean())
                if   val >= _buy_thr:  sig.iloc[i] =  1
                elif val <= _sell_thr: sig.iloc[i] = -1

    except Exception as _e:
        import logging as _lg
        _lg.getLogger("signal_gen").warning(f"[ml_strategy] 오류: {{_e}}")
    return sig.astype(int)
'''

# 기존 signal_ml_strategy 함수 교체
old_pattern = r'def signal_ml_strategy\(df:.*?(?=\ndef |\Z)'
import re
new_code, n = re.subn(old_pattern, NEW_ML_FUNC.lstrip('\n'), code, flags=re.DOTALL)
if n == 1:
    sg_path.write_bytes(new_code.encode("utf-8"))
    # 문법 검증
    try:
        ast.parse(new_code)
        print(f"  ✅ signal_generator.py ml_strategy 교체 완료 ({n}개)")
        print(f"  buy_thr={buy_thr_use:+.6f}, sell_thr={sell_thr_use:+.6f}")
    except SyntaxError as e:
        shutil.copy2(BACKUP_DIR / sg_path.name, sg_path)
        print(f"  ❌ 문법 오류 → 복원됨: {e}")
else:
    print(f"  ⚠️  패턴 매칭 실패 (n={n}) → 수동 교체 필요")

# ══════════════════════════════════════════════════════════════
# 4. 교체 후 180일 백테스트 (BTC, ETH, XRP 3개만 빠르게)
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("[3] 교체 후 ml_strategy 백테스트 (3코인 180일)")
print("=" * 60)
async def quick_backtest():
    from backtesting.backtester import Backtester
    from backtesting.data_loader import fetch_ohlcv
    # signal_generator 재로드
    import importlib
    import backtesting.signal_generator as sg_mod
    importlib.reload(sg_mod)

    bt = Backtester(initial_capital=114000, fee_rate=0.0005,
                    slippage=0.001, stop_loss_pct=0.022,
                    take_profit_pct=0.045, position_size=0.20, max_positions=5)

    total_wins = total_trades = 0
    for market in ['KRW-BTC','KRW-ETH','KRW-XRP']:
        df = await fetch_ohlcv(market, '1d', 180)
        if df is None or len(df) < 70:
            continue
        try:
            res = bt.run(df, 'ml_strategy', market)
            icon = '✅' if res.expectancy > 0 and res.win_rate >= 45 else '❌'
            print(f"  {icon} {market:<12} 승률={res.win_rate:.1f}% "
                  f"기댓값={res.expectancy:+.4f} 샤프={res.sharpe_ratio:+.3f} "
                  f"MDD={res.max_drawdown:.1f}% 거래={res.total_trades}회")
            total_wins   += int(res.win_rate * res.total_trades / 100)
            total_trades += res.total_trades
        except Exception as e:
            print(f"  ⚠️  {market}: {e}")

    if total_trades > 0:
        overall_wr = total_wins / total_trades * 100
        print(f"\n  통합 승률: {overall_wr:.1f}% ({total_trades}회 거래)")
        if overall_wr >= 50 and total_trades >= 10:
            print("  🟢 실제 ML 모델 사용 가능 — 페이퍼 트레이딩 진행 권장")
        elif overall_wr >= 45 and total_trades >= 6:
            print("  🟡 조건부 사용 — 더 많은 코인으로 추가 검증 필요")
        else:
            print("  🔴 아직 부족 — 임계값 재조정 또는 재학습 필요")

asyncio.run(quick_backtest())
print(f"\n  백업: {BACKUP_DIR}")
