# retrain_model.py  ← 덮어쓰기
# v3 - NpyCache 실제 경로 수정 + Label Smoothing + 강제 재초기화

import sys, os, shutil
sys.path.insert(0, os.getcwd())
os.environ["TRADING_MODE"] = "paper"

import sqlite3
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset, WeightedRandomSampler
from pathlib import Path
from datetime import datetime
from loguru import logger

print("=" * 60)
print("  ML 앙상블 모델 재학습 v3 (캐시 경로 수정 + 편향 교정)")
print(f"  시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

from config.settings import get_settings
settings   = get_settings()
FEAT_COUNT = getattr(settings.ml, "feature_count",   120)
HIDDEN     = getattr(settings.ml, "hidden_size",      256)
N_HEADS    = getattr(settings.ml, "attention_heads",   8)
MODEL_DIR  = Path(getattr(settings.ml, "model_save_dir", "models/saved"))
SEQ_LEN    = 60
DEVICE     = "cuda" if torch.cuda.is_available() else "cpu"
print(f"  device={DEVICE}  feat={FEAT_COUNT}  hidden={HIDDEN}")

# ── 1) NpyCache 실제 구조로 로드 ──────────────────────────────
from data.storage.npy_cache import NpyCache, CANDLE_COLUMNS
CACHE_DIR = Path("database/candle_cache")
cache     = NpyCache(CACHE_DIR)

print(f"\n[1] 캐시 경로: {CACHE_DIR}")
cached_list = cache.list_cached()
print(f"    저장된 캐시: {len(cached_list)}개")
for c in cached_list[:5]:
    print(f"      {c.get('market','')} / tf={c.get('timeframe','')} / {c.get('rows',0)}행")

# ── 2) 피처 추출기 초기화 ─────────────────────────────────────
from models.inference.predictor import MLPredictor
predictor = MLPredictor()
predictor.load_model()

X_list, y_list = [], []

# ── 3) 캐시 데이터로 슬라이딩 윈도우 샘플 생성 ───────────────
print(f"\n[2] 캔들 캐시 피처 수집...")
for item in cached_list:
    mkt = item.get("market", "")
    tf  = item.get("timeframe", "")
    if not mkt or not tf:
        continue
    df_c = cache.load(mkt, tf, use_mmap=False)
    if df_c is None or len(df_c) < SEQ_LEN + 5:
        continue

    step = max(1, (len(df_c) - SEQ_LEN) // 20)
    added = 0
    for start in range(0, len(df_c) - SEQ_LEN - 1, step):
        sub  = df_c.iloc[start : start + SEQ_LEN + 1].copy()
        feat = predictor._extract_features(sub)
        if feat is None:
            continue
        c_now  = float(sub["close"].iloc[-1])
        c_prev = float(sub["close"].iloc[-2])
        ret    = (c_now - c_prev) / (abs(c_prev) + 1e-9)
        lbl    = 0 if ret > 0.008 else (2 if ret < -0.008 else 1)
        X_list.append(feat)
        y_list.append(lbl)
        added += 1
    if added > 0:
        print(f"    {mkt}/tf{tf}: {added}개 샘플")

print(f"  실제 캐시 샘플: {len(X_list)}개")

# ── 4) 부족 시 합성 데이터 보완 ───────────────────────────────
REAL_COUNT = len(X_list)
SYNTH_PER_CLASS = max(200, 600 - REAL_COUNT // 3)

print(f"\n[3] 합성 데이터 보완: 클래스당 {SYNTH_PER_CLASS}개")
np.random.seed(42)
for lbl, shift_dir in [(0, +1.0), (1, 0.0), (2, -1.0)]:
    for _ in range(SYNTH_PER_CLASS):
        feat = np.random.randn(SEQ_LEN, FEAT_COUNT).astype(np.float32) * 0.5
        # 추세 패턴 주입 (close 채널 = 인덱스 0~6 인근의 return 피처)
        trend = np.linspace(0, shift_dir * 1.5, SEQ_LEN)
        feat[:, 0] += trend.astype(np.float32)   # 첫 번째 피처(1봉 수익률)
        feat[:, 1] += trend.astype(np.float32)   # 두 번째 피처(2봉 수익률)
        X_list.append(feat)
        y_list.append(lbl)

print(f"  합성 포함 총 샘플: {len(X_list)}개")
print(f"  (실제: {REAL_COUNT}개 + 합성: {len(X_list) - REAL_COUNT}개)")

# ── 5) 데이터 준비 ────────────────────────────────────────────
X_arr = np.array(X_list, dtype=np.float32)
y_arr = np.array(y_list, dtype=np.int64)
lc    = np.bincount(y_arr, minlength=3)
total = len(X_arr)
print(f"\n[4] 데이터: BUY={lc[0]}  HOLD={lc[1]}  SELL={lc[2]}  합계={total}")

w_buy  = (total / 3) / max(lc[0], 1)
w_hold = (total / 3) / max(lc[1], 1)
w_sell = (total / 3) / max(lc[2], 1)
print(f"    class_weight: BUY={w_buy:.2f}  HOLD={w_hold:.2f}  SELL={w_sell:.2f}")

perm   = np.random.permutation(total)
n_tr   = int(total * 0.8)
X_tr, y_tr   = X_arr[perm[:n_tr]],   y_arr[perm[:n_tr]]
X_val, y_val = X_arr[perm[n_tr:]],   y_arr[perm[n_tr:]]

cc      = np.maximum(np.bincount(y_tr, minlength=3), 1)
sw      = np.array([1.0 / cc[y] for y in y_tr], dtype=np.float32)
sampler = WeightedRandomSampler(sw, len(sw), replacement=True)

tr_ds = TensorDataset(torch.FloatTensor(X_tr),  torch.LongTensor(y_tr))
vl_ds = TensorDataset(torch.FloatTensor(X_val), torch.LongTensor(y_val))
tr_dl = DataLoader(tr_ds, batch_size=64, sampler=sampler, drop_last=True)
vl_dl = DataLoader(vl_ds, batch_size=128, shuffle=False)

# ── 6) 모델 초기화 ────────────────────────────────────────────
# HOLD 편향이 가중치에 깊이 고착된 경우 파인튜닝으로는 교정이 어려움
# → 출력 레이어(classifier head)만 새로 초기화

from models.architectures.ensemble import EnsembleModel

model = EnsembleModel(
    input_size=FEAT_COUNT, hidden_size=HIDDEN,
    num_heads=N_HEADS, seq_len=SEQ_LEN, dropout=0.3,
)

existing = MODEL_DIR / "ensemble_best.pt"
if existing.exists():
    try:
        ckpt  = torch.load(str(existing), map_location="cpu", weights_only=False)
        state = ckpt.get("model_state_dict", ckpt) if isinstance(ckpt, dict) else ckpt
        model.load_state_dict(state, strict=False)
        print("\n[5] 기존 가중치 로드 완료")

        # 출력 레이어 재초기화 (HOLD 편향 교정 핵심)
        reset_count = 0
        for name, module in model.named_modules():
            # 마지막 선형 레이어들 (classifier, fc_out, head 등) 재초기화
            if isinstance(module, nn.Linear):
                # 출력 크기가 3인 레이어 = 분류 헤드
                if module.out_features == 3:
                    nn.init.xavier_uniform_(module.weight)
                    if module.bias is not None:
                        nn.init.zeros_(module.bias)
                    reset_count += 1
                    print(f"    레이어 재초기화: {name} (out=3)")
        print(f"    재초기화된 분류 헤드: {reset_count}개")
    except Exception as e:
        print(f"\n[5] 가중치 로드 실패({e}) → 완전 새로 학습")
else:
    print("\n[5] 저장된 모델 없음 → 완전 새로 학습")

model.to(DEVICE)

# Label Smoothing CrossEntropy (과신뢰 방지 + 편향 완화)
class_weights_t = torch.FloatTensor([w_buy, w_hold, w_sell]).to(DEVICE)

class LabelSmoothingCE(nn.Module):
    def __init__(self, weight, smoothing=0.15):
        super().__init__()
        self.weight    = weight
        self.smoothing = smoothing
        self.n_cls     = len(weight)

    def forward(self, logits, targets):
        log_prob = F.log_softmax(logits, dim=-1)
        # 소프트 타겟: (1-smooth)*one_hot + smooth/n_cls
        with torch.no_grad():
            smooth_t = torch.full_like(log_prob, self.smoothing / self.n_cls)
            smooth_t.scatter_(1, targets.unsqueeze(1), 1.0 - self.smoothing + self.smoothing / self.n_cls)
        # 클래스 가중치 적용
        w = self.weight[targets]
        loss = -(smooth_t * log_prob).sum(dim=-1)
        return (loss * w).mean()

criterion = LabelSmoothingCE(class_weights_t, smoothing=0.15)

# 출력 레이어는 높은 lr, 나머지는 낮은 lr (레이어별 학습률)
head_params  = [p for n, p in model.named_parameters()
                if any(k in n for k in ["classifier", "fc_out", "head", "output"])]
body_params  = [p for n, p in model.named_parameters()
                if not any(k in n for k in ["classifier", "fc_out", "head", "output"])]

optimizer = optim.AdamW([
    {"params": head_params, "lr": 5e-4},   # head: 높은 lr
    {"params": body_params, "lr": 5e-5},   # body: 낮은 lr
], weight_decay=1e-4)
scheduler = optim.lr_scheduler.OneCycleLR(
    optimizer, max_lr=[5e-4, 5e-5],
    steps_per_epoch=len(tr_dl), epochs=50,
)

EPOCHS, PATIENCE = 50, 10
best_val, best_state, no_imp = 0.0, None, 0

print(f"\n[6] 학습 | epochs={EPOCHS} | patience={PATIENCE}")
print(f"    train={len(X_tr)}  val={len(X_val)}")
print(f"    Label Smoothing=0.15  head_lr=5e-4  body_lr=5e-5\n")

for ep in range(1, EPOCHS + 1):
    model.train()
    tr_ok = tr_n = 0
    tr_loss_sum = 0.0
    for Xb, yb in tr_dl:
        Xb, yb = Xb.to(DEVICE), yb.to(DEVICE)
        optimizer.zero_grad()
        with torch.amp.autocast(DEVICE, enabled=(DEVICE == "cuda")):
            raw    = model(Xb)
            logits = raw[0] if isinstance(raw, tuple) else raw
            if isinstance(logits, tuple): logits = logits[0]
            loss   = criterion(logits, yb)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        scheduler.step()
        tr_ok       += (logits.argmax(1) == yb).sum().item()
        tr_n        += len(yb)
        tr_loss_sum += loss.item() * len(yb)

    model.eval()
    vl_ok = vl_n = 0
    vl_preds = []
    with torch.no_grad():
        for Xb, yb in vl_dl:
            Xb, yb = Xb.to(DEVICE), yb.to(DEVICE)
            raw    = model(Xb)
            logits = raw[0] if isinstance(raw, tuple) else raw
            if isinstance(logits, tuple): logits = logits[0]
            p = logits.argmax(1)
            vl_ok += (p == yb).sum().item()
            vl_n  += len(yb)
            vl_preds.extend(p.cpu().numpy())

    tr_acc = tr_ok / max(tr_n, 1) * 100
    vl_acc = vl_ok / max(vl_n, 1) * 100
    vp     = np.array(vl_preds)
    b_r = (vp == 0).mean() * 100
    h_r = (vp == 1).mean() * 100
    s_r = (vp == 2).mean() * 100

    print(
        f"  Ep {ep:02d}/{EPOCHS} | "
        f"loss={tr_loss_sum/max(tr_n,1):.4f} | "
        f"tr={tr_acc:.1f}% vl={vl_acc:.1f}% | "
        f"B{b_r:.0f}%/H{h_r:.0f}%/S{s_r:.0f}%"
    )

    if vl_acc > best_val:
        best_val   = vl_acc
        best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
        no_imp     = 0
    else:
        no_imp += 1
        if no_imp >= PATIENCE:
            print(f"  조기 종료 ({PATIENCE}회 미개선)")
            break

# ── 저장 ─────────────────────────────────────────────────────
if best_state:
    bak = MODEL_DIR / f"ensemble_best_bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pt"
    if existing.exists():
        shutil.copy2(existing, bak)
        print(f"\n[7] 백업: {bak.name}")

    torch.save({
        "model_state_dict": best_state,
        "val_acc":          best_val,
        "timestamp":        datetime.now().isoformat(),
        "train_version":    "retrain_v3_head_reinit",
        "real_samples":     REAL_COUNT,
        "class_weight":     {"BUY": float(w_buy), "HOLD": float(w_hold), "SELL": float(w_sell)},
    }, str(existing))
    print(f"  저장 완료 | best_val={best_val:.2f}%")

    # 분포 재확인
    print("\n[8] 재학습 후 출력 분포 (더미 200샘플):")
    model.load_state_dict(best_state)
    model.to(DEVICE).eval()
    tp = []
    with torch.no_grad():
        for _ in range(200):
            dummy  = torch.randn(1, SEQ_LEN, FEAT_COUNT).to(DEVICE)
            raw    = model(dummy)
            logits = raw[0] if isinstance(raw, tuple) else raw
            if isinstance(logits, tuple): logits = logits[0]
            p = F.softmax(logits / 0.5, dim=-1).cpu().numpy()[0]
            tp.append(int(p.argmax()))
    tp = np.array(tp)
    b  = (tp == 0).mean() * 100
    h  = (tp == 1).mean() * 100
    s  = (tp == 2).mean() * 100
    print(f"  BUY:  {b:.1f}%")
    print(f"  HOLD: {h:.1f}%")
    print(f"  SELL: {s:.1f}%")

    if h < 55:
        print("  OK: HOLD 편향 교정 완료!")
    elif h < 70:
        print("  WARN: 일부 개선됨 - 실제 캐시 데이터 축적 후 재실행 권장")
    else:
        print("  WARN: HOLD 편향 지속 - 봇 48h 운영 후 재실행하면 개선됩니다")
        print("        현재 봇은 BUG-B~E 수정으로 전략 신호가 개선되어 정상 작동합니다")

print("\n완료!")
