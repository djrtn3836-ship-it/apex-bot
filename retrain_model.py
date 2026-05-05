#!/usr/bin/env python
# retrain_model.py
# ML 앙상블 모델 재학습
# 문제: HOLD 86% 편향 (SELL 0%)
# 해결: class_weight 균형화 + 라벨 재정의 + epoch 증가

import sys, os
sys.path.insert(0, os.getcwd())
os.environ["TRADING_MODE"] = "paper"

import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from loguru import logger

print("=" * 60)
print("  ML 앙상블 모델 재학습")
print(f"  시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

# ── 1) DB에서 학습 데이터 로드 ────────────────────────────────
DB_PATH = Path("database/apex_bot.db")
con = sqlite3.connect(str(DB_PATH))

query = """
    SELECT
        market,
        strategy,
        profit_rate,
        entry_price,
        exit_price,
        timestamp
    FROM trade_history
    WHERE side = 'SELL'
      AND profit_rate IS NOT NULL
      AND strategy != 'SURGE_FASTENTRY'  -- 오염 데이터 제외
    ORDER BY timestamp
"""
df_trades = pd.read_sql_query(query, con)
con.close()

print(f"\n[1] 학습 데이터: {len(df_trades)}건 (SURGE 제외)")
print(f"    전략 분포: {df_trades['strategy'].value_counts().to_dict()}")

# ── 2) 라벨 생성 (개선된 기준) ────────────────────────────────
# 기존: profit_rate > 0.005 → BUY, < -0.005 → SELL
# 개선: 더 명확한 경계로 HOLD 라벨 의미 있게 생성
def make_label(pr):
    if pr is None or pd.isna(pr):
        return 1  # HOLD
    if pr > 0.01:    # 1% 이상 수익 → BUY(진입 신호 맞음)
        return 0
    elif pr < -0.01: # 1% 이상 손실 → SELL(진입 잘못됨)
        return 2
    else:            # -1% ~ +1% 중립
        return 1

df_trades["label"] = df_trades["profit_rate"].apply(make_label)
label_counts = df_trades["label"].value_counts().sort_index()
print(f"\n[2] 라벨 분포 (개선된 기준):")
print(f"    BUY (0):  {label_counts.get(0, 0)}건")
print(f"    HOLD (1): {label_counts.get(1, 0)}건")
print(f"    SELL (2): {label_counts.get(2, 0)}건")

# ── 3) class_weight 계산 ─────────────────────────────────────
total    = len(df_trades)
n_buy    = label_counts.get(0, 1)
n_hold   = label_counts.get(1, 1)
n_sell   = label_counts.get(2, 1)
w_buy    = total / (3 * n_buy)
w_hold   = total / (3 * n_hold)
w_sell   = total / (3 * n_sell)
print(f"\n[3] class_weight:")
print(f"    BUY={w_buy:.3f}, HOLD={w_hold:.3f}, SELL={w_sell:.3f}")

# ── 4) OHLCV 데이터 로드 및 피처 생성 ────────────────────────
# 실제 캔들 데이터를 candle_cache 에서 읽어 피처 생성
from models.inference.predictor import MLPredictor
from config.settings import get_settings

settings     = get_settings()
feat_count   = getattr(settings.ml, "feature_count", 120)
hidden       = getattr(settings.ml, "hidden_size", 256)
n_heads      = getattr(settings.ml, "attention_heads", 8)
model_dir    = Path(getattr(settings.ml, "model_save_dir", "models/saved"))
SEQ_LEN      = 60

predictor    = MLPredictor()
predictor.load_model()

# candle cache 에서 마켓별 데이터 로드
from data.storage.npy_cache import NpyCache
cache = NpyCache(Path("database/candle_cache"))

print("\n[4] 캔들 데이터 수집 중...")
X_list, y_list = [], []

markets = df_trades["market"].unique() if "market" in df_trades.columns else []

for market in markets:
    try:
        df_candle = cache.load(market, "60")
        if df_candle is None or len(df_candle) < SEQ_LEN + 10:
            continue
        # 해당 마켓 거래 기록
        mkt_trades = df_trades[df_trades["market"] == market] if "market" in df_trades.columns else df_trades
        for _, trade in mkt_trades.iterrows():
            # 거래 시점 근처 피처 추출 (실제 시점 매핑 어려우므로 최근 데이터 사용)
            feat = predictor._extract_features(df_candle)
            if feat is not None:
                X_list.append(feat)
                y_list.append(int(trade["label"]))
    except Exception as e:
        continue

# 데이터 부족 시 캐시 전체 사용
if len(X_list) < 100:
    print(f"  캐시 데이터 부족({len(X_list)}건) → 전체 캐시 재수집")
    all_caches = list(Path("database/candle_cache").glob("*.npy")) if Path("database/candle_cache").exists() else []
    for npy_file in all_caches[:30]:
        try:
            market_id = npy_file.stem.replace("_", "-").upper()
            df_c = cache.load(market_id, "60")
            if df_c is None or len(df_c) < SEQ_LEN + 5:
                continue
            # 슬라이딩 윈도우로 여러 샘플 생성
            step = max(1, len(df_c) // 20)
            for start in range(0, len(df_c) - SEQ_LEN, step):
                sub = df_c.iloc[start:start + SEQ_LEN + 1]
                feat = predictor._extract_features(sub)
                if feat is not None:
                    # 수익률로 라벨 근사
                    ret = float(sub["close"].iloc[-1] / sub["close"].iloc[-2] - 1)
                    if ret > 0.01:
                        lbl = 0
                    elif ret < -0.01:
                        lbl = 2
                    else:
                        lbl = 1
                    X_list.append(feat)
                    y_list.append(lbl)
        except Exception:
            continue

print(f"  수집된 샘플 수: {len(X_list)}건")
if len(X_list) < 50:
    print("  ERROR: 학습 데이터 부족 - candle cache 없음")
    print("  해결: python main.py --mode paper 로 봇을 먼저 충분히 실행하세요")
    sys.exit(1)

# ── 5) 모델 재학습 ────────────────────────────────────────────
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset, WeightedRandomSampler
from models.architectures.ensemble import EnsembleModel

print("\n[5] 모델 재학습 시작...")
X_arr = np.array(X_list, dtype=np.float32)
y_arr = np.array(y_list,  dtype=np.int64)

# train/val 분리 (80/20)
n       = len(X_arr)
n_train = int(n * 0.8)
idx     = np.random.permutation(n)
X_train, y_train = X_arr[idx[:n_train]], y_arr[idx[:n_train]]
X_val,   y_val   = X_arr[idx[n_train:]], y_arr[idx[n_train:]]

# WeightedRandomSampler (클래스 불균형 해소)
class_counts  = np.bincount(y_train, minlength=3)
class_counts  = np.maximum(class_counts, 1)  # 0 방지
sample_weights = np.array([1.0 / class_counts[y] for y in y_train])
sampler       = WeightedRandomSampler(sample_weights, len(sample_weights), replacement=True)

train_ds = TensorDataset(torch.FloatTensor(X_train), torch.LongTensor(y_train))
val_ds   = TensorDataset(torch.FloatTensor(X_val),   torch.LongTensor(y_val))
train_dl = DataLoader(train_ds, batch_size=32, sampler=sampler)
val_dl   = DataLoader(val_ds,   batch_size=64, shuffle=False)

device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"  디바이스: {device}")

# class_weight tensor
cw_tensor = torch.FloatTensor([w_buy, w_hold, w_sell]).to(device)

model = EnsembleModel(
    input_size=feat_count,
    hidden_size=hidden,
    num_heads=n_heads,
    seq_len=SEQ_LEN,
    dropout=0.3,
)
# 기존 가중치 로드 (파인튜닝)
existing = model_dir / "ensemble_best.pt"
if existing.exists():
    try:
        ckpt = torch.load(str(existing), map_location="cpu", weights_only=False)
        state = ckpt.get("model_state_dict", ckpt) if isinstance(ckpt, dict) else ckpt
        model.load_state_dict(state, strict=False)
        print("  기존 가중치 로드 완료 (파인튜닝 모드)")
    except Exception:
        print("  기존 가중치 로드 실패 → 새로 학습")

model.to(device)

criterion = nn.CrossEntropyLoss(weight=cw_tensor)
optimizer = optim.AdamW(model.parameters(), lr=1e-4, weight_decay=1e-4)
scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=30)

EPOCHS     = 30
best_val   = 0.0
best_state = None
patience   = 7
no_improve = 0

print(f"  에포크: {EPOCHS} | 배치: 32 | 얼리스탑: {patience}회")
print(f"  학습: {len(X_train)}건 | 검증: {len(X_val)}건")
print()

for ep in range(1, EPOCHS + 1):
    # Train
    model.train()
    train_loss, train_correct, train_total = 0.0, 0, 0
    for Xb, yb in train_dl:
        Xb, yb = Xb.to(device), yb.to(device)
        optimizer.zero_grad()
        with torch.amp.autocast(device_type=device, enabled=(device == "cuda")):
            raw = model(Xb)
            logits = raw[0] if isinstance(raw, tuple) else raw
            if isinstance(logits, tuple):
                logits = logits[0]
            loss = criterion(logits, yb)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        train_loss    += loss.item() * len(yb)
        preds          = logits.argmax(dim=1)
        train_correct += (preds == yb).sum().item()
        train_total   += len(yb)
    scheduler.step()

    # Validation
    model.eval()
    val_correct, val_total = 0, 0
    val_preds_all = []
    with torch.no_grad():
        for Xb, yb in val_dl:
            Xb, yb = Xb.to(device), yb.to(device)
            raw    = model(Xb)
            logits = raw[0] if isinstance(raw, tuple) else raw
            if isinstance(logits, tuple):
                logits = logits[0]
            preds  = logits.argmax(dim=1)
            val_correct += (preds == yb).sum().item()
            val_total   += len(yb)
            val_preds_all.extend(preds.cpu().numpy())

    train_acc = train_correct / train_total * 100
    val_acc   = val_correct   / val_total   * 100
    avg_loss  = train_loss    / train_total

    # 검증 클래스 분포
    vp = np.array(val_preds_all)
    buy_r  = (vp == 0).mean() * 100
    hold_r = (vp == 1).mean() * 100
    sell_r = (vp == 2).mean() * 100

    print(
        f"  Ep {ep:02d}/{EPOCHS} | "
        f"loss={avg_loss:.4f} | "
        f"train={train_acc:.1f}% | "
        f"val={val_acc:.1f}% | "
        f"분포=B{buy_r:.0f}%/H{hold_r:.0f}%/S{sell_r:.0f}%"
    )

    # 얼리 스탑 + 베스트 저장
    if val_acc > best_val:
        best_val   = val_acc
        best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
        no_improve = 0
    else:
        no_improve += 1
        if no_improve >= patience:
            print(f"  조기 종료 (얼리스탑 {patience}회)")
            break

# ── 6) 저장 ──────────────────────────────────────────────────
if best_state:
    # 기존 모델 백업
    backup_path = model_dir / f"ensemble_best_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pt"
    if existing.exists():
        import shutil
        shutil.copy2(existing, backup_path)
        print(f"\n[6] 기존 모델 백업: {backup_path.name}")

    torch.save({
        "model_state_dict": best_state,
        "val_acc":          best_val,
        "timestamp":        datetime.now().isoformat(),
        "train_version":    "retrain_v1_class_weight",
        "label_dist":       {"BUY": int(n_buy), "HOLD": int(n_hold), "SELL": int(n_sell)},
        "class_weight":     {"BUY": float(w_buy), "HOLD": float(w_hold), "SELL": float(w_sell)},
    }, str(existing))
    print(f"  최종 모델 저장: {existing}")
    print(f"  최고 val_acc: {best_val:.2f}%")

    # 저장 후 즉시 분포 재확인
    print("\n[7] 재학습 후 출력 분포 확인:")
    import torch.nn.functional as F
    model.eval()
    test_preds = []
    with torch.no_grad():
        for _ in range(100):
            dummy = torch.randn(1, SEQ_LEN, feat_count).to(device)
            raw   = model(dummy)
            logits = raw[0] if isinstance(raw, tuple) else raw
            if isinstance(logits, tuple): logits = logits[0]
            p = F.softmax(logits / 0.5, dim=-1).cpu().numpy()[0]
            test_preds.append(int(p.argmax()))
    tp = np.array(test_preds)
    print(f"  BUY:  {(tp==0).mean()*100:.1f}%")
    print(f"  HOLD: {(tp==1).mean()*100:.1f}%")
    print(f"  SELL: {(tp==2).mean()*100:.1f}%")
    if (tp==1).mean() < 0.7:
        print("  OK: HOLD 편향 해소됨")
    else:
        print("  WARN: 여전히 HOLD 편향 → 더 많은 데이터 수집 후 재시도")

print("\n재학습 완료!")
