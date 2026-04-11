# train_retrain.py  v2 ← 앙상블 모델 정확한 파라미터로 재훈련
# -*- coding: utf-8 -*-
import os, sys, time, warnings, shutil
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
import requests
from pathlib import Path
from datetime import datetime

# ── 설정 ──────────────────────────────────────────────────────────────
MARKETS = [
    # 대형 (높은 유동성, 신뢰도 높음)
    "KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP", "KRW-ADA",
    "KRW-DOGE", "KRW-AVAX", "KRW-DOT", "KRW-LINK", "KRW-ATOM",
    # 중형 (다양한 패턴)
    "KRW-MATIC", "KRW-UNI", "KRW-AAVE", "KRW-SAND", "KRW-CHZ",
    "KRW-EOS", "KRW-TRX", "KRW-XLM", "KRW-ALGO", "KRW-NEAR",
    # 소형 (고변동성 패턴)
    "KRW-FTM", "KRW-THETA", "KRW-VET", "KRW-ZIL", "KRW-HBAR",
    "KRW-ICX", "KRW-STMX", "KRW-BTT", "KRW-ONT", "KRW-WAVES",
]
TIMEFRAMES = ["days", "minutes/240", "minutes/60", "minutes/15", "minutes/5"]
CANDLES_PER = 1000   # 페이지네이션으로 수집
FORWARD_N   = 24     # 예측 범위 확대
BUY_THR     = 0.008  # 레이블 민감도 조정
SELL_THR    = -0.008 # 레이블 민감도 조정
SEQ_LEN     = 60
INPUT_SIZE  = 120   # EnsembleModel 기본값 맞춤
EPOCHS      = 50     # 샘플 증가에 맞춰 확대
BATCH_SIZE  = 128    # 샘플 증가에 맞춰 확대
LR          = 1e-4
TEMPERATURE = 0.5
SAVE_PATH   = Path("models/saved/ensemble_best.pt")

print("=" * 60)
print("APEX BOT ML 재훈련 v2 (앙상블 모델)")
print("=" * 60)

# ══════════════════════════════════════════════════════════════════════
# STEP 1: 캔들 수집
# ══════════════════════════════════════════════════════════════════════
print("\n[STEP 1] 캔들 데이터 수집 중...")

def fetch_candles(market, tf, count=200):
    """Upbit API 페이지네이션으로 최대 count개 캔들 수집.
    API 제한: 1회 최대 200개 → count//200 + 1회 호출
    """
    import time as _time
    url       = f"https://api.upbit.com/v1/candles/{tf}"
    per_page  = 200
    collected = []
    to_param  = None  # 기준 시각 (None=최신부터)

    pages = (count + per_page - 1) // per_page  # 올림 나눗셈
    for page in range(pages):
        need = min(per_page, count - len(collected))
        params = {"market": market, "count": need}
        if to_param:
            params["to"] = to_param
        try:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 429:
                print(f"  [429] {market}/{tf} Rate Limit → 2초 대기")
                _time.sleep(2)
                r = requests.get(url, params=params, timeout=10)
            data = r.json()
            if not isinstance(data, list) or len(data) == 0:
                break
            collected.extend(data)
            # 다음 페이지: 가장 오래된 캔들 시각 기준
            oldest = min(data, key=lambda x: x["candle_date_time_utc"])
            to_param = oldest["candle_date_time_utc"]
            _time.sleep(0.12)  # Rate Limit 방지 (초당 8회 제한)
            if len(data) < need:
                break  # 더 이상 데이터 없음
        except Exception as e:
            print(f"  오류 ({market}/{tf} page{page}): {e}")
            break

    if not collected:
        return pd.DataFrame()

    df = pd.DataFrame(collected).rename(columns={
        "candle_date_time_kst": "datetime",
        "opening_price":        "open",
        "high_price":           "high",
        "low_price":            "low",
        "trade_price":          "close",
        "candle_acc_trade_volume": "volume",
    })
    # 필요한 컬럼만 선택 (없으면 건너뜀)
    cols = [c for c in ["datetime","open","high","low","close","volume"] if c in df.columns]
    df = df[cols].drop_duplicates("datetime")
    df = df.sort_values("datetime").reset_index(drop=True)
    for col in ["open","high","low","close","volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna()
    return df

all_dfs = []
for market in MARKETS:
    for tf in TIMEFRAMES:
        df = fetch_candles(market, tf, CANDLES_PER)
        if len(df) >= SEQ_LEN + FORWARD_N + 5:
            df["market"] = market
            all_dfs.append(df)
        time.sleep(0.12)

print(f"  총 {len(all_dfs)}개 시리즈 수집 완료")

# ══════════════════════════════════════════════════════════════════════
# STEP 2: 피처 120개 생성 + 레이블
# ══════════════════════════════════════════════════════════════════════
print("\n[STEP 2] 피처 120개 추출 + 레이블 생성 중...")

def ema(x, n):
    s = np.zeros(len(x)); s[0] = x[0]; k = 2/(n+1)
    for i in range(1, len(x)):
        s[i] = x[i]*k + s[i-1]*(1-k)
    return s

def rsi(x, n=14):
    d = np.diff(x, prepend=x[0])
    up = np.where(d>0,d,0.); dn = np.where(d<0,-d,0.)
    au = pd.Series(up).ewm(span=n).mean().values
    ad = pd.Series(dn).ewm(span=n).mean().values
    return 100 - 100/(1 + au/(ad+1e-9))

def build_features(df):
    """120개 피처 생성 (EnsembleModel input_size=120 맞춤)"""
    c = df["close"].values.astype(float)
    v = df["volume"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    o = df["open"].values.astype(float)
    n = len(c)

    feats = {}

    # 가격 관련 (20개)
    for period in [5,10,20,50,100,200]:
        feats[f"ema{period}"] = ema(c, period) / (c + 1e-9) - 1
    feats["close_ret1"]  = np.diff(c, prepend=c[0]) / (c + 1e-9)
    feats["close_ret3"]  = (c - np.roll(c,3)) / (np.roll(c,3) + 1e-9)
    feats["close_ret5"]  = (c - np.roll(c,5)) / (np.roll(c,5) + 1e-9)
    feats["close_ret10"] = (c - np.roll(c,10))/ (np.roll(c,10)+ 1e-9)
    feats["hl_ratio"]    = (h - l) / (c + 1e-9)
    feats["oc_ratio"]    = (c - o) / (o + 1e-9)
    feats["high_norm"]   = (h - c) / (c + 1e-9)
    feats["low_norm"]    = (c - l) / (c + 1e-9)
    feats["close_norm"]  = (c - c.mean()) / (c.std() + 1e-9)
    feats["log_return"]  = np.log(c / (np.roll(c,1) + 1e-9))
    feats["body_size"]   = np.abs(c - o) / (h - l + 1e-9)
    feats["upper_wick"]  = (h - np.maximum(c,o)) / (h - l + 1e-9)
    feats["lower_wick"]  = (np.minimum(c,o) - l) / (h - l + 1e-9)

    # 모멘텀 (20개)
    for period in [6,14,21]:
        feats[f"rsi{period}"] = (rsi(c, period) - 50) / 50
    ema12 = ema(c,12); ema26 = ema(c,26)
    macd = ema12 - ema26
    macd_sig = ema(macd, 9)
    feats["macd"]        = macd / (np.abs(macd).max() + 1e-9)
    feats["macd_signal"] = macd_sig / (np.abs(macd_sig).max() + 1e-9)
    feats["macd_hist"]   = (macd - macd_sig) / (np.abs(macd-macd_sig).max() + 1e-9)
    for period in [5,10,20]:
        roll_std = pd.Series(c).rolling(period, min_periods=1).std().fillna(0).values
        feats[f"volatility{period}"] = roll_std / (c + 1e-9)
    # Stochastic
    for k_period in [9,14]:
        low_min  = pd.Series(l).rolling(k_period, min_periods=1).min().values
        high_max = pd.Series(h).rolling(k_period, min_periods=1).max().values
        feats[f"stoch{k_period}"] = (c - low_min) / (high_max - low_min + 1e-9) - 0.5
    # Williams %R
    feats["williams_r"] = (pd.Series(h).rolling(14,min_periods=1).max().values - c) / \
                          (pd.Series(h).rolling(14,min_periods=1).max().values -
                           pd.Series(l).rolling(14,min_periods=1).min().values + 1e-9) - 0.5
    # ROC
    for period in [5,10,20]:
        feats[f"roc{period}"] = (c - np.roll(c,period)) / (np.roll(c,period) + 1e-9)
    # CCI
    tp = (h + l + c) / 3
    ma_tp = pd.Series(tp).rolling(20, min_periods=1).mean().values
    md_tp = pd.Series(tp).rolling(20, min_periods=1).apply(
        lambda x: np.abs(x - x.mean()).mean(), raw=True).fillna(0).values
    feats["cci"] = (tp - ma_tp) / (0.015 * md_tp + 1e-9) / 200

    # 볼린저 (15개)
    for period in [10, 20, 30]:
        ma   = pd.Series(c).rolling(period, min_periods=1).mean().values
        std  = pd.Series(c).rolling(period, min_periods=1).std().fillna(0).values
        feats[f"bb_upper{period}"] = (ma + 2*std - c) / (c + 1e-9)
        feats[f"bb_lower{period}"] = (c - (ma - 2*std)) / (c + 1e-9)
        feats[f"bb_pct{period}"]   = (c - (ma-2*std)) / (4*std + 1e-9) - 0.5
    feats["bb_squeeze"] = (pd.Series(c).rolling(20,min_periods=1).std().fillna(0).values /
                          (pd.Series(c).rolling(20,min_periods=1).std().fillna(0).rolling(
                              50,min_periods=1).mean().values + 1e-9) - 1)

    # ATR / 변동성 (10개)
    tr = np.maximum(h-l, np.maximum(np.abs(h-np.roll(c,1)), np.abs(l-np.roll(c,1))))
    for period in [7,14,21,28]:
        feats[f"atr{period}"] = pd.Series(tr).rolling(period,min_periods=1).mean().values/(c+1e-9)
    feats["atr_ratio"] = feats["atr14"] / (feats["atr28"] + 1e-9)
    # 가격 채널
    for period in [10,20,50]:
        feats[f"channel_pos{period}"] = (c - pd.Series(l).rolling(period,min_periods=1).min().values) / \
            (pd.Series(h).rolling(period,min_periods=1).max().values -
             pd.Series(l).rolling(period,min_periods=1).min().values + 1e-9) - 0.5
    feats["dc_width"] = (pd.Series(h).rolling(20,min_periods=1).max().values -
                         pd.Series(l).rolling(20,min_periods=1).min().values) / (c + 1e-9)

    # 거래량 (20개)
    feats["volume_norm"]  = (v - v.mean()) / (v.std() + 1e-9)
    feats["log_volume"]   = np.log(v + 1) / (np.log(v + 1).mean() + 1e-9) - 1
    for period in [5,10,20,50]:
        vm = pd.Series(v).rolling(period, min_periods=1).mean().values
        feats[f"vol_ratio{period}"] = v / (vm + 1e-9) - 1
    # OBV
    obv = np.cumsum(np.where(np.diff(c,prepend=c[0])>0, v, -v))
    feats["obv_norm"] = (obv - obv.mean()) / (obv.std() + 1e-9)
    # VWAP proxy
    vwap = np.cumsum(c*v) / (np.cumsum(v) + 1e-9)
    feats["vwap_ratio"] = c / (vwap + 1e-9) - 1
    # Volume price trend
    vpt = np.cumsum(v * np.diff(c,prepend=c[0]) / (np.roll(c,1)+1e-9))
    feats["vpt_norm"] = (vpt - vpt.mean()) / (vpt.std() + 1e-9)
    # MFI
    money_flow = tp * v
    pos_mf = np.where(np.diff(tp,prepend=tp[0])>0, money_flow, 0.)
    neg_mf = np.where(np.diff(tp,prepend=tp[0])<0, money_flow, 0.)
    pos_sum = pd.Series(pos_mf).rolling(14,min_periods=1).sum().values
    neg_sum = pd.Series(neg_mf).rolling(14,min_periods=1).sum().values
    feats["mfi"] = (100 - 100/(1+pos_sum/(neg_sum+1e-9))) / 100 - 0.5
    # Chaikin
    clv = ((c-l)-(h-c))/(h-l+1e-9)
    feats["chaikin"] = pd.Series(clv*v).rolling(14,min_periods=1).sum().values / (v.sum()+1e-9)
    feats["vol_std20"] = pd.Series(v).rolling(20,min_periods=1).std().fillna(0).values / (v.mean()+1e-9)
    feats["vol_trend"]  = (pd.Series(v).rolling(5,min_periods=1).mean().values -
                           pd.Series(v).rolling(20,min_periods=1).mean().values) / (v.mean()+1e-9)

    # 시장 구조 / 추가 (나머지 채워서 120개 맞춤)
    feats["price_accel"] = np.diff(np.diff(c, prepend=c[0]), prepend=0) / (c + 1e-9)
    feats["trend_strength"] = np.abs(feats["ema5"] - feats["ema20"])
    feats["mean_rev"]    = (c - pd.Series(c).rolling(50,min_periods=1).mean().values) / \
                           (pd.Series(c).rolling(50,min_periods=1).std().fillna(1).values + 1e-9)
    feats["high_dist"]   = (pd.Series(h).rolling(20,min_periods=1).max().values - c) / (c+1e-9)
    feats["low_dist"]    = (c - pd.Series(l).rolling(20,min_periods=1).min().values) / (c+1e-9)
    feats["pivot_pos"]   = feats["high_dist"] - feats["low_dist"]
    feats["vol_price_corr"] = pd.Series(c*v).rolling(10,min_periods=1).corr(
        pd.Series(c)).fillna(0).values
    feats["candle_count"] = np.arange(n) / n - 0.5

    # 정확히 120개 맞추기
    feat_arr = np.column_stack([v for v in feats.values()])
    current = feat_arr.shape[1]
    if current < INPUT_SIZE:
        pad = np.zeros((n, INPUT_SIZE - current))
        feat_arr = np.hstack([feat_arr, pad])
    elif current > INPUT_SIZE:
        feat_arr = feat_arr[:, :INPUT_SIZE]

    # NaN/Inf 처리
    feat_arr = np.nan_to_num(feat_arr, nan=0.0, posinf=1.0, neginf=-1.0)
    feat_arr = np.clip(feat_arr, -10, 10)
    return feat_arr

X_list, y_list = [], []
label_counts = {0:0, 1:0, 2:0}

for df in all_dfs:
    feat_arr = build_features(df)
    closes   = df["close"].values

    for i in range(SEQ_LEN, len(df) - FORWARD_N):
        future_ret = (closes[i+FORWARD_N] - closes[i]) / (closes[i] + 1e-9)
        label = 0 if future_ret >= BUY_THR else (2 if future_ret <= SELL_THR else 1)
        window = feat_arr[i-SEQ_LEN:i]
        if window.shape == (SEQ_LEN, INPUT_SIZE):
            X_list.append(window.astype(np.float32))
            y_list.append(label)
            label_counts[label] += 1

X = np.array(X_list, dtype=np.float32)
y = np.array(y_list, dtype=np.int64)
total = len(y)
print(f"  총 샘플: {total}개")
print(f"  BUY : {label_counts[0]}개 ({label_counts[0]/total*100:.1f}%)")
print(f"  HOLD: {label_counts[1]}개 ({label_counts[1]/total*100:.1f}%)")
print(f"  SELL: {label_counts[2]}개 ({label_counts[2]/total*100:.1f}%)")
print(f"  피처 shape: {X.shape}")

# 균형 조정
max_cnt = max(label_counts.values())
X_bal, y_bal = [], []
for cls in [0,1,2]:
    idx = np.where(y==cls)[0]
    if len(idx) == 0: continue
    rep = int(np.ceil(max_cnt/len(idx)))
    idx_r = np.tile(idx,rep)[:max_cnt]
    np.random.shuffle(idx_r)
    X_bal.append(X[idx_r]); y_bal.append(y[idx_r])
X_bal = np.concatenate(X_bal); y_bal = np.concatenate(y_bal)
perm  = np.random.permutation(len(y_bal))
X_bal = X_bal[perm];  y_bal = y_bal[perm]
print(f"  균형 조정 후: {len(y_bal)}개 (각 클래스 {max_cnt}개)")

split   = int(len(y_bal)*0.85)
X_train, X_val = X_bal[:split], X_bal[split:]
y_train, y_val = y_bal[:split], y_bal[split:]
print(f"  Train: {len(y_train)} | Val: {len(y_val)}")

# ══════════════════════════════════════════════════════════════════════
# STEP 3: 앙상블 모델 재훈련
# ══════════════════════════════════════════════════════════════════════
print("\n[STEP 3] 앙상블 모델 재훈련 중...")

import torch
import torch.nn as nn
import torch.nn.functional as F_t
from torch.utils.data import DataLoader, TensorDataset

device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"  Device: {device}")

from models.architectures.ensemble import EnsembleModel
model = EnsembleModel(
    input_size  = INPUT_SIZE,
    hidden_size = 256,
    num_heads   = 8,
    seq_len     = SEQ_LEN,
    dropout     = 0.2,
).to(device)

# 기존 가중치 로드 (파인튜닝)
if SAVE_PATH.exists():
    try:
        backup_name = "ensemble_best_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".pt"
        shutil.copy(str(SAVE_PATH), str(SAVE_PATH.parent / backup_name))
        state = torch.load(str(SAVE_PATH), map_location=device, weights_only=False)
        if isinstance(state, dict) and "model_state_dict" in state:
            model.load_state_dict(state["model_state_dict"], strict=False)
        print(f"  기존 가중치 로드 완료 (파인튜닝) → 백업: {backup_name}")
    except Exception as e:
        print(f"  기존 가중치 로드 실패 (새로 훈련): {e}")
else:
    print("  새 앙상블 모델 초기화")

train_loader = DataLoader(
    TensorDataset(torch.FloatTensor(X_train), torch.LongTensor(y_train)),
    batch_size=BATCH_SIZE, shuffle=True, num_workers=0)
val_loader = DataLoader(
    TensorDataset(torch.FloatTensor(X_val), torch.LongTensor(y_val)),
    batch_size=BATCH_SIZE, shuffle=False, num_workers=0)

criterion = nn.CrossEntropyLoss(label_smoothing=0.1)
optimizer = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=1e-4)
scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=EPOCHS)

best_val_acc = 0.0
best_state   = None
patience     = 8
no_improve   = 0

for epoch in range(1, EPOCHS+1):
    model.train()
    train_loss = 0.0
    for xb, yb in train_loader:
        xb, yb = xb.to(device), yb.to(device)
        optimizer.zero_grad()
        out = model(xb)
        if isinstance(out, tuple): out = out[0]
        loss = criterion(out, yb)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        train_loss += loss.item()
    scheduler.step()

    model.eval()
    correct = total_v = 0
    conf_sum = 0.0
    with torch.no_grad():
        for xb, yb in val_loader:
            xb, yb = xb.to(device), yb.to(device)
            out = model(xb)
            if isinstance(out, tuple): out = out[0]
            proba = F_t.softmax(out / TEMPERATURE, dim=-1)
            pred  = proba.argmax(dim=-1)
            correct  += (pred==yb).sum().item()
            total_v  += yb.size(0)
            conf_sum += proba.max(dim=-1).values.sum().item()

    val_acc  = correct/total_v*100
    avg_conf = conf_sum/total_v

    if epoch % 5 == 0 or epoch == 1:
        print(f"  Epoch {epoch:3d}/{EPOCHS} | "
              f"loss={train_loss/len(train_loader):.4f} | "
              f"val_acc={val_acc:.1f}% | avg_conf={avg_conf:.3f}")

    if val_acc > best_val_acc:
        best_val_acc = val_acc
        best_state   = {k: v.cpu().clone() for k, v in model.state_dict().items()}
        no_improve   = 0
    else:
        no_improve += 1
        if no_improve >= patience:
            print(f"  Early stop at epoch {epoch}")
            break

# ══════════════════════════════════════════════════════════════════════
# STEP 4: 저장 + 신뢰도 분포 리포트
# ══════════════════════════════════════════════════════════════════════
print("\n[STEP 4] 저장 + 신뢰도 분포 확인...")

if best_state:
    model.load_state_dict(best_state)
torch.save({
    "model_state_dict": model.state_dict(),
    "val_acc":    best_val_acc,
    "timestamp":  datetime.now().isoformat(),
    "temperature": TEMPERATURE,
    "input_size": INPUT_SIZE,
    "seq_len":    SEQ_LEN,
}, str(SAVE_PATH))
print(f"  모델 저장: {SAVE_PATH}")
print(f"  Best val_acc: {best_val_acc:.1f}%")

model.eval()
confs = []
pred_counts = {0:0, 1:0, 2:0}
with torch.no_grad():
    for xb, _ in val_loader:
        xb = xb.to(device)
        out = model(xb)
        if isinstance(out, tuple): out = out[0]
        proba = F_t.softmax(out / TEMPERATURE, dim=-1)
        confs.extend(proba.max(dim=-1).values.cpu().numpy().tolist())
        preds = proba.argmax(dim=-1).cpu().numpy()
        for p in preds:
            pred_counts[int(p)] += 1

confs = np.array(confs)
total_pred = sum(pred_counts.values())
print(f"\n  신뢰도 분포 (T={TEMPERATURE}):")
print(f"  평균={confs.mean():.3f} | 최소={confs.min():.3f} | 최대={confs.max():.3f}")
print(f"  0.70+ 비율: {(confs>=0.70).mean()*100:.1f}%")
print(f"  0.60+ 비율: {(confs>=0.60).mean()*100:.1f}%")
print(f"  0.50+ 비율: {(confs>=0.50).mean()*100:.1f}%")
print(f"\n  예측 분포:")
print(f"  BUY : {pred_counts[0]}개 ({pred_counts[0]/total_pred*100:.1f}%)")
print(f"  HOLD: {pred_counts[1]}개 ({pred_counts[1]/total_pred*100:.1f}%)")
print(f"  SELL: {pred_counts[2]}개 ({pred_counts[2]/total_pred*100:.1f}%)")

print("\n" + "="*60)
print(f"재훈련 완료! val_acc={best_val_acc:.1f}%")
print("봇 재시작: python main.py --mode paper")
print("="*60)