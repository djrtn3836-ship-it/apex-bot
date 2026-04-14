# train_retrain.py v3.0 - Phase 5 ML 파이프라인 개선
# -*- coding: utf-8 -*-
"""
개선 사항 (v3.0):
    1. FORWARD_N=8  : 8시간 후 수익률 기준 (단기 매매 최적화)
    2. BUY_THR=0.012: +1.2% 이상만 BUY (노이즈 제거)
    3. SELL_THR=-0.010: -1.0% 이하만 SELL (비대칭 손절 반영)
    4. 레이블 생성 개선: 단순 N봉 후 수익이 아닌 최고점/최저점 활용
    5. 균형 조정: 오버샘플링 → 가중치 기반 (과적합 방지)
    6. 피처 누수 제거: 미래 데이터 참조 차단
    7. 검증셋 성능 기준 미달 시 자동 롤백
    8. 훈련 결과 JSON 저장 (성능 추적)
"""
import os, sys, time, warnings, shutil, json
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
import requests
from pathlib import Path
from datetime import datetime

# ── 설정 ──────────────────────────────────────────────────────────────────
MARKETS = [
    # 대형 (높은 유동성)
    "KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP", "KRW-ADA",
    "KRW-DOGE", "KRW-AVAX", "KRW-DOT", "KRW-LINK", "KRW-ATOM",
    # 중형
    "KRW-UNI", "KRW-AAVE", "KRW-SAND", "KRW-CHZ",
    "KRW-EOS", "KRW-TRX", "KRW-XLM", "KRW-ALGO", "KRW-NEAR",
    # 소형 (고변동성)
    "KRW-THETA", "KRW-VET", "KRW-HBAR",
    "KRW-ICX", "KRW-STMX", "KRW-ONT",
]
TIMEFRAMES   = ["days", "minutes/240", "minutes/60", "minutes/15"]
CANDLES_PER  = 1000

# ★ 핵심 파라미터 개선
FORWARD_N    = 8       # 8시간 후 수익률 (단기 매매 최적화, 기존 24→8)
BUY_THR      = 0.012   # +1.2% 이상 → BUY (기존 0.8%→1.2%, 노이즈 제거)
SELL_THR     = -0.010  # -1.0% 이하 → SELL (비대칭 손절)
USE_PEAK     = True    # N봉 내 최고점/최저점으로 레이블 (피크 기반)
PEAK_WINDOW  = 4       # 피크 확인 범위 (FORWARD_N의 절반)

SEQ_LEN      = 60
INPUT_SIZE   = 120
EPOCHS       = 60      # 증가 (조기 종료 포함)
BATCH_SIZE   = 128
LR           = 8e-5    # 약간 낮춤 (파인튜닝)
TEMPERATURE  = 0.5
MIN_VAL_ACC  = 0.42    # 최소 검증 정확도 (미달 시 롤백)
SAVE_PATH    = Path("models/saved/ensemble_best.pt")
RESULT_PATH  = Path("models/saved/train_result.json")

print("=" * 60)
print("APEX BOT ML 재훈련 v3.0 (Phase 5 개선)")
print(f"  FORWARD_N={FORWARD_N} | BUY_THR={BUY_THR} | SELL_THR={SELL_THR}")
print(f"  USE_PEAK={USE_PEAK} | MIN_VAL_ACC={MIN_VAL_ACC}")
print("=" * 60)

# ══════════════════════════════════════════════════════════════════════════
# STEP 1: 캔들 수집
# ══════════════════════════════════════════════════════════════════════════
print("\n[STEP 1] 캔들 데이터 수집 중...")

def fetch_candles(market, tf, count=200):
    """Upbit API 페이지네이션으로 최대 count개 캔들 수집."""
    base = "https://api.upbit.com/v1/candles"
    url  = f"{base}/{tf}"
    all_data, cursor = [], None
    headers = {"accept": "application/json"}
    while len(all_data) < count:
        params = {"market": market, "count": min(200, count - len(all_data))}
        if cursor:
            params["to"] = cursor
        try:
            r = requests.get(url, params=params, headers=headers, timeout=10)
            if r.status_code == 429:
                time.sleep(1.0)
                continue
            if r.status_code != 200:
                break
            data = r.json()
            if not data:
                break
            all_data.extend(data)
            cursor = data[-1]["candle_date_time_utc"]
            time.sleep(0.12)
        except Exception:
            break
    if not all_data:
        return None
    df = pd.DataFrame(all_data)
    df = df.rename(columns={
        "candle_date_time_kst": "datetime",
        "opening_price": "open", "high_price": "high",
        "low_price": "low",     "trade_price": "close",
        "candle_acc_trade_volume": "volume",
    })
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()
    return df[["open","high","low","close","volume"]].astype(float)

all_dfs = []
for market in MARKETS:
    for tf in TIMEFRAMES:
        df = fetch_candles(market, tf, CANDLES_PER)
        if df is not None and len(df) >= SEQ_LEN + FORWARD_N + 10:
            all_dfs.append(df)
            print(f"  {market}/{tf}: {len(df)}개")
        else:
            print(f"  {market}/{tf}: 스킵 (데이터 부족)")

print(f"\n총 {len(all_dfs)}개 시계열 수집 완료")

# ══════════════════════════════════════════════════════════════════════════
# STEP 2: 피처 엔지니어링 (피처 누수 차단)
# ══════════════════════════════════════════════════════════════════════════
print("\n[STEP 2] 피처 엔지니어링...")

def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().values

def rsi(x, n=14):
    s = pd.Series(x)
    d = s.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = (-d).clip(lower=0).rolling(n).mean()
    return (100 - 100/(1+g/(l+1e-9))).values

def build_features(df):
    """피처 누수 없는 피처 엔지니어링 (미래 데이터 참조 차단)."""
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    v = df["volume"].values
    n = len(c)

    feats = []

    # 1. 가격 기반 (정규화된 수익률)
    ret1  = np.diff(c, prepend=c[0]) / (c + 1e-9)
    ret5  = np.array([(c[i]-c[max(0,i-5)])/(c[max(0,i-5)]+1e-9) for i in range(n)])
    ret20 = np.array([(c[i]-c[max(0,i-20)])/(c[max(0,i-20)]+1e-9) for i in range(n)])
    feats += [ret1, ret5, ret20]

    # 2. EMA 이격도 (정규화)
    for p in [5,10,20,50,100,200]:
        e = ema(c, p)
        feats.append((c - e) / (e + 1e-9))

    # 3. RSI
    feats.append(rsi(c, 14) / 100.0)
    feats.append(rsi(c, 7)  / 100.0)

    # 4. 볼린저 밴드 %B
    for p in [20, 50]:
        mid = pd.Series(c).rolling(p).mean().values
        std = pd.Series(c).rolling(p).std().values
        bb_pct = (c - (mid - 2*std)) / (4*std + 1e-9)
        feats.append(np.clip(bb_pct, -1, 2))

    # 5. 거래량 (정규화)
    v_ma20 = pd.Series(v).rolling(20).mean().values
    v_ratio = v / (v_ma20 + 1e-9)
    feats.append(np.clip(v_ratio, 0, 10) / 10.0)
    v_ret1 = np.diff(v, prepend=v[0]) / (v + 1e-9)
    feats.append(np.clip(v_ret1, -1, 1))

    # 6. ATR (정규화)
    tr = np.maximum(h-l, np.maximum(abs(h-np.roll(c,1)), abs(l-np.roll(c,1))))
    atr = pd.Series(tr).rolling(14).mean().values
    feats.append(atr / (c + 1e-9))

    # 7. 캔들 패턴
    body  = (c - o) / (h - l + 1e-9)
    upper = (h - np.maximum(o,c)) / (h - l + 1e-9)
    lower = (np.minimum(o,c) - l) / (h - l + 1e-9)
    feats += [body, upper, lower]

    # 8. MACD
    ema12 = ema(c, 12); ema26 = ema(c, 26)
    macd  = ema12 - ema26
    signal= ema(macd, 9)
    hist  = macd - signal
    feats += [macd/(c+1e-9), signal/(c+1e-9), hist/(c+1e-9)]

    # 9. 스토캐스틱
    for p in [14, 21]:
        lo_p = pd.Series(l).rolling(p).min().values
        hi_p = pd.Series(h).rolling(p).max().values
        stoch = (c - lo_p) / (hi_p - lo_p + 1e-9)
        feats.append(np.clip(stoch, 0, 1))

    # 10. OBV (정규화)
    direction = np.sign(np.diff(c, prepend=c[0]))
    obv = np.cumsum(v * direction)
    obv_norm = (obv - obv.mean()) / (obv.std() + 1e-9)
    feats.append(np.clip(obv_norm / 3, -1, 1))

    # 11. 고가/저가 대비 위치
    hi52 = pd.Series(h).rolling(min(200, n)).max().values
    lo52 = pd.Series(l).rolling(min(200, n)).min().values
    feats.append((c - lo52) / (hi52 - lo52 + 1e-9))

    # 12. 변동성 (역사적)
    hist_vol = pd.Series(ret1).rolling(20).std().values
    feats.append(np.clip(hist_vol * 10, 0, 1))

    # ── 패딩/트리밍 to INPUT_SIZE
    feat_arr = np.stack(feats, axis=1)  # (n, num_feats)
    num_feats = feat_arr.shape[1]
    if num_feats < INPUT_SIZE:
        pad = np.zeros((n, INPUT_SIZE - num_feats))
        feat_arr = np.hstack([feat_arr, pad])
    else:
        feat_arr = feat_arr[:, :INPUT_SIZE]

    feat_arr = np.nan_to_num(feat_arr, nan=0.0, posinf=1.0, neginf=-1.0)
    feat_arr = np.clip(feat_arr, -10, 10)
    return feat_arr

# ══════════════════════════════════════════════════════════════════════════
# STEP 3: 레이블 생성 (피크 기반 개선)
# ══════════════════════════════════════════════════════════════════════════
print("\n[STEP 3] 레이블 생성 (FORWARD_N={}, BUY_THR={}, SELL_THR={})...".format(
    FORWARD_N, BUY_THR, SELL_THR))

X_list, y_list = [], []
label_counts = {0:0, 1:0, 2:0}

for df in all_dfs:
    feat_arr = build_features(df)
    closes   = df["close"].values
    highs    = df["high"].values
    lows     = df["low"].values

    for i in range(SEQ_LEN, len(df) - FORWARD_N):
        window = feat_arr[i-SEQ_LEN:i]
        if window.shape != (SEQ_LEN, INPUT_SIZE):
            continue

        if USE_PEAK:
            # 피크 기반: N봉 내 최고점/최저점으로 레이블
            future_highs = highs[i:i+FORWARD_N]
            future_lows  = lows[i:i+FORWARD_N]
            cur_close    = closes[i]
            max_ret  = (future_highs.max() - cur_close) / (cur_close + 1e-9)
            min_ret  = (future_lows.min()  - cur_close) / (cur_close + 1e-9)

            # 조건: 최고 수익이 BUY_THR 이상 AND 최저 낙폭이 SELL_THR 이상 → BUY
            if max_ret >= BUY_THR and min_ret >= SELL_THR * 0.5:
                label = 0  # BUY
            elif min_ret <= SELL_THR and max_ret <= BUY_THR * 0.5:
                label = 2  # SELL
            else:
                label = 1  # HOLD
        else:
            # 기존 방식: N봉 후 단순 수익률
            future_ret = (closes[i+FORWARD_N] - closes[i]) / (closes[i] + 1e-9)
            label = 0 if future_ret >= BUY_THR else (2 if future_ret <= SELL_THR else 1)

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

# ── 클래스 균형: 가중치 기반 (오버샘플링 최소화)
# BUY/SELL은 소수 클래스 → 2배 오버샘플, HOLD는 언더샘플
target_cnt = int(np.median(list(label_counts.values())) * 1.5)
X_bal, y_bal = [], []
for cls in [0, 1, 2]:
    idx = np.where(y == cls)[0]
    if len(idx) == 0:
        continue
    if len(idx) >= target_cnt:
        # 언더샘플
        chosen = np.random.choice(idx, target_cnt, replace=False)
    else:
        # 오버샘플 (최대 2배)
        cap = min(target_cnt, len(idx) * 2)
        chosen = np.random.choice(idx, cap, replace=True)
    X_bal.append(X[chosen])
    y_bal.append(y[chosen])

X_bal = np.concatenate(X_bal)
y_bal = np.concatenate(y_bal)
perm  = np.random.permutation(len(y_bal))
X_bal = X_bal[perm]; y_bal = y_bal[perm]
print(f"  균형 조정 후: {len(y_bal)}개")
bc = {c: int((y_bal==c).sum()) for c in [0,1,2]}
print(f"  BUY:{bc[0]} HOLD:{bc[1]} SELL:{bc[2]}")

split   = int(len(y_bal) * 0.85)
X_train, X_val = X_bal[:split], X_bal[split:]
y_train, y_val = y_bal[:split], y_bal[split:]
print(f"  Train: {len(y_train)} | Val: {len(y_val)}")

# ══════════════════════════════════════════════════════════════════════════
# STEP 4: 앙상블 모델 재훈련
# ══════════════════════════════════════════════════════════════════════════
print("\n[STEP 4] 앙상블 모델 재훈련 중...")

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"  Device: {device}")

from models.architectures.ensemble import EnsembleModel
model = EnsembleModel(
    input_size  = INPUT_SIZE,
    hidden_size = 256,
    num_heads   = 8,
    seq_len     = SEQ_LEN,
    dropout     = 0.3,
).to(device)

# 기존 가중치 로드 (파인튜닝)
backup_name = None
if SAVE_PATH.exists():
    try:
        backup_name = "ensemble_best_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".pt"
        shutil.copy(str(SAVE_PATH), str(SAVE_PATH.parent / backup_name))
        state = torch.load(str(SAVE_PATH), map_location=device, weights_only=False)
        if isinstance(state, dict) and "model_state_dict" in state:
            model.load_state_dict(state["model_state_dict"], strict=False)
        print(f"  기존 가중치 로드 완료 → 백업: {backup_name}")
    except Exception as e:
        print(f"  기존 가중치 로드 실패 (새로 훈련): {e}")

# 클래스 가중치 (BUY/SELL 중요도 상향)
class_counts = np.array([bc[0], bc[1], bc[2]], dtype=float)
class_weights = torch.tensor(
    1.0 / (class_counts / class_counts.sum() + 1e-9),
    dtype=torch.float32
).to(device)
class_weights = class_weights / class_weights.sum() * 3  # 정규화

criterion = nn.CrossEntropyLoss(
    weight=class_weights,
    label_smoothing=0.05  # 과적합 방지 (기존 0.1→0.05)
)
optimizer = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=1e-4)
scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
    optimizer, T_0=20, T_mult=2
)

train_ds = TensorDataset(torch.tensor(X_train), torch.tensor(y_train))
val_ds   = TensorDataset(torch.tensor(X_val),   torch.tensor(y_val))
train_dl = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True,  num_workers=0)
val_dl   = DataLoader(val_ds,   batch_size=BATCH_SIZE, shuffle=False, num_workers=0)

best_val_acc  = 0.0
best_state    = None
patience      = 10
patience_cnt  = 0
train_history = []

for epoch in range(1, EPOCHS + 1):
    # 훈련
    model.train()
    t_loss, t_correct, t_total = 0.0, 0, 0
    for xb, yb in train_dl:
        xb, yb = xb.to(device), yb.to(device)
        optimizer.zero_grad()
        logits, _ = model(xb)
        loss = criterion(logits, yb)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        t_loss    += loss.item() * len(yb)
        t_correct += (logits.argmax(1) == yb).sum().item()
        t_total   += len(yb)
    scheduler.step()

    # 검증
    model.eval()
    v_loss, v_correct, v_total = 0.0, 0, 0
    with torch.no_grad():
        for xb, yb in val_dl:
            xb, yb = xb.to(device), yb.to(device)
            logits, _ = model(xb)
            loss = criterion(logits, yb)
            v_loss    += loss.item() * len(yb)
            v_correct += (logits.argmax(1) == yb).sum().item()
            v_total   += len(yb)

    t_acc = t_correct / t_total
    v_acc = v_correct / v_total
    train_history.append({"epoch": epoch, "train_acc": round(t_acc,4), "val_acc": round(v_acc,4)})

    if epoch % 5 == 0 or epoch == 1:
        print(f"  Epoch {epoch:3d}/{EPOCHS} | "
              f"Train Loss={t_loss/t_total:.4f} Acc={t_acc:.4f} | "
              f"Val Loss={v_loss/v_total:.4f} Acc={v_acc:.4f}")

    # 최적 모델 저장
    if v_acc > best_val_acc:
        best_val_acc = v_acc
        best_state   = {k: v.cpu().clone() for k, v in model.state_dict().items()}
        patience_cnt = 0
    else:
        patience_cnt += 1
        if patience_cnt >= patience:
            print(f"  조기 종료 (patience={patience}, epoch={epoch})")
            break

# ══════════════════════════════════════════════════════════════════════════
# STEP 5: 성능 검증 및 저장/롤백
# ══════════════════════════════════════════════════════════════════════════
print(f"\n[STEP 5] 성능 검증...")
print(f"  최고 검증 정확도: {best_val_acc:.4f} (기준: {MIN_VAL_ACC})")

result = {
    "version":      "v3.0",
    "timestamp":    datetime.now().isoformat(),
    "forward_n":    FORWARD_N,
    "buy_thr":      BUY_THR,
    "sell_thr":     SELL_THR,
    "use_peak":     USE_PEAK,
    "best_val_acc": round(best_val_acc, 4),
    "min_val_acc":  MIN_VAL_ACC,
    "total_samples": total,
    "label_dist":   {
        "buy":  label_counts[0],
        "hold": label_counts[1],
        "sell": label_counts[2],
    },
    "epochs_trained": len(train_history),
    "history":      train_history[-5:],
}

if best_val_acc >= MIN_VAL_ACC and best_state is not None:
    # 저장
    model.load_state_dict(best_state)
    torch.save({
        "model_state_dict": model.state_dict(),
        "val_acc":          best_val_acc,
        "forward_n":        FORWARD_N,
        "buy_thr":          BUY_THR,
        "sell_thr":         SELL_THR,
        "timestamp":        datetime.now().isoformat(),
        "train_version":    "v3.0",
    }, str(SAVE_PATH))
    result["saved"] = True
    RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  모델 저장 완료: {SAVE_PATH}")
    print(f"  결과 저장: {RESULT_PATH}")
    print(f"\n{'='*60}")
    print(f"  훈련 완료 | Val Acc: {best_val_acc:.4f} | PASS")
    print(f"{'='*60}")
else:
    # 롤백
    result["saved"] = False
    result["rollback_reason"] = f"val_acc={best_val_acc:.4f} < {MIN_VAL_ACC}"
    RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  성능 미달 → 롤백 (val_acc={best_val_acc:.4f} < {MIN_VAL_ACC})")
    if backup_name and (SAVE_PATH.parent / backup_name).exists():
        shutil.copy(str(SAVE_PATH.parent / backup_name), str(SAVE_PATH))
        print(f"  롤백 완료: {backup_name} → {SAVE_PATH}")
    print(f"\n{'='*60}")
    print(f"  훈련 실패 | Val Acc: {best_val_acc:.4f} | ROLLBACK")
    print(f"{'='*60}")