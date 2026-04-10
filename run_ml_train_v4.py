# run_ml_train_v4.py
"""APEX BOT — ML   v4
v3   :
  1. : minute60 × 200 × 5 → day(1d) × 500 ( )
  2. Loss: FocalLoss  → CrossEntropyLoss + label_smoothing=0.05
  3. threshold: 0.6% → 1.0% ( )
  4. horizon: 3 → 5 ( )
  5.   10  ( )
  6.  augmentation:   
: python run_ml_train_v4.py"""
import asyncio, sys
import numpy as np
from pathlib import Path

ROOT = Path(".")
sys.path.insert(0, str(ROOT))


async def collect_data_v4():
    """- minute60: 500 ( v2 , )
    -  486 × 10 = 4860"""
    from config.settings import get_settings
    from data.collectors.rest_collector import RestCollector
    from data.processors.candle_processor import CandleProcessor
    import pandas as pd

    settings  = get_settings()
    markets   = settings.trading.target_markets
    collector = RestCollector()
    processor = CandleProcessor()
    all_dfs   = []

    print(f"\n[1/5] OHLCV  (minute60 × 500 × {len(markets)}코인)...")
    for market in markets:
        try:
            # v2와 동일한 방식 (검증됨)
            df = await collector.get_ohlcv(market, "minute60", 500)
            if df is None or len(df) < 100:
                print(f"    {market}:   — ")
                continue
            df_proc = await processor.process(market, df, "60")
            if df_proc is None or len(df_proc) < 100:
                print(f"    {market}:   — ")
                continue
            all_dfs.append(df_proc)
            print(f"   {market}: {len(df_proc)}행")
            await asyncio.sleep(0.35)
        except Exception as e:
            print(f"   {market}: {e}")

    if not all_dfs:
        return None, None

    combined = pd.concat(all_dfs, ignore_index=True)
    print(f"\n   : {len(combined)}행 × {len(combined.columns)}컬럼")
    return combined, all_dfs


def extract_features_v4(trainer, df, threshold=0.010, horizon=5):
    """v4  
    - threshold 1.0% ( BUY/SELL )
    - horizon 5 ()
    -   10 ( )
    -  :"""
    from config.settings import get_settings

    settings = get_settings()
    seq_len  = settings.ml.sequence_length  # 60

    base_cols = [c for c in [
        "open", "high", "low", "close", "volume",
        "ema20", "ema50", "ema200", "rsi", "macd", "macd_signal", "macd_hist",
        "bb_upper", "bb_mid", "bb_lower", "bb_pct", "bb_width",
        "atr", "atr_pct", "stoch_k", "stoch_d", "vwap",
        "adx", "di_plus", "di_minus", "obv", "cci", "mfi",
        "vol_ratio", "supertrend_dir",
    ] if c in df.columns]

    if len(base_cols) < 5:
        raise ValueError(f" : {len(base_cols)}개")

    data   = df[base_cols].values.astype(np.float32)
    close  = df["close"].values.astype(np.float32)
    volume = df["volume"].values.astype(np.float32)
    high   = df["high"].values.astype(np.float32)
    low    = df["low"].values.astype(np.float32)
    n      = len(close)

    # ── 추가 피처 (10개, 검증된 것만) ────────────────────────
    extra = np.zeros((n, 10), dtype=np.float32)
    for i in range(20, n):
        # 1. 추세 강도
        extra[i, 0] = np.clip(
            (close[i] - close[i-20]) / (close[i-20] + 1e-8), -0.5, 0.5
        )
        # 2. 가격 가속도
        if i >= 3:
            v1 = close[i-1] - close[i-2]
            v2 = close[i]   - close[i-1]
            extra[i, 1] = np.clip(
                (v2 - v1) / (close[i] + 1e-8), -0.1, 0.1
            )
        # 3. 변동성 레짐
        if i >= 14:
            atr_avg   = np.mean(high[i-14:i] - low[i-14:i])
            cur_range = high[i] - low[i]
            extra[i, 2] = np.clip(
                cur_range / (atr_avg + 1e-8) - 1, -2, 2
            )
        # 4. 거래량 가속도
        if i >= 5:
            vol_ma5 = np.mean(volume[i-5:i])
            extra[i, 3] = np.clip(
                volume[i] / (vol_ma5 + 1e-8) - 1, -3, 3
            )
        # 5. 20봉 고점 돌파
        if i >= 20:
            extra[i, 4] = 1.0 if close[i] > np.max(high[i-20:i]) else 0.0
        # 6. 20봉 저점 이탈
        if i >= 20:
            extra[i, 5] = 1.0 if close[i] < np.min(low[i-20:i]) else 0.0
        # 7. 캔들 방향 연속성
        if i >= 3:
            dirs = np.sign(np.diff(close[i-3:i+1]))
            extra[i, 6] = np.sum(dirs) / 3.0
        # 8. 거래량-가격 다이버전스
        if i >= 10:
            pr = (close[i] - close[i-10]) / (close[i-10] + 1e-8)
            vc = (np.mean(volume[i-5:i]) - np.mean(volume[i-10:i-5])) \
                 / (np.mean(volume[i-10:i-5]) + 1e-8)
            extra[i, 7] = np.clip(pr - vc, -2, 2)
        # 9. 매수 압력
        extra[i, 8] = (close[i] - low[i]) / (high[i] - low[i] + 1e-8)
        # 10. 52봉 위치
        if i >= 52:
            h52 = np.max(high[i-52:i])
            l52 = np.min(low[i-52:i])
            extra[i, 9] = (close[i] - l52) / (h52 - l52 + 1e-8)

    data_combined = np.hstack([data, extra])

    # ── 정규화 ─────────────────────────────────────────────────
    data_norm = trainer._rolling_normalize(data_combined)
    data_norm = np.nan_to_num(data_norm, nan=0, posinf=1, neginf=-1)

    target_f = settings.ml.feature_count  # 120
    if data_norm.shape[1] < target_f:
        pad = np.zeros((len(data_norm), target_f - data_norm.shape[1]))
        data_norm = np.hstack([data_norm, pad])
    else:
        data_norm = data_norm[:, :target_f]

    # ── 레이블 생성 (threshold=1.0%) ─────────────────────────
    labels    = np.ones(n, dtype=int)
    buy_cnt   = sell_cnt = hold_cnt = 0

    for i in range(n - horizon):
        fut = (close[i + horizon] - close[i]) / (close[i] + 1e-8)
        if fut > threshold:
            labels[i] = 0; buy_cnt  += 1
        elif fut < -threshold:
            labels[i] = 2; sell_cnt += 1
        else:
            hold_cnt += 1

    print(f"\n    (threshold={threshold*100:.1f}%, horizon={horizon}):")
    print(f"    BUY : {buy_cnt:4d} ({buy_cnt/n*100:.1f}%)")
    print(f"    HOLD: {hold_cnt:4d} ({hold_cnt/n*100:.1f}%)")
    print(f"    SELL: {sell_cnt:4d} ({sell_cnt/n*100:.1f}%)")
    print(f"    : {data_combined.shape[1]} "
          f"( {len(base_cols)} + 추가 10)")

    # ── 시퀀스 생성 ───────────────────────────────────────────
    X, y = [], []
    for i in range(seq_len, len(data_norm) - horizon):
        X.append(data_norm[i - seq_len:i])
        y.append(labels[i])

    X = np.array(X, dtype=np.float32)
    y = np.array(y, dtype=np.int64)

    # ── 데이터 증강: BUY/SELL 샘플에 가우시안 노이즈 ─────────
    buy_idx  = np.where(y == 0)[0]
    sell_idx = np.where(y == 2)[0]

    aug_X, aug_y = [], []
    for idx in np.concatenate([buy_idx, sell_idx]):
        noise = np.random.normal(0, 0.005, X[idx].shape).astype(np.float32)
        aug_X.append(X[idx] + noise)
        aug_y.append(y[idx])

    if aug_X:
        X = np.vstack([X, np.array(aug_X)])
        y = np.concatenate([y, np.array(aug_y)])
        # 셔플
        perm = np.random.permutation(len(X))
        X, y = X[perm], y[perm]
        print(f"      : {len(X)}개 "
              f"(원본 {len(perm)-len(aug_X)} + 증강 {len(aug_X)})")

    return X, y


def train_v4(trainer, X, y):
    """v4 
    - CrossEntropyLoss + label_smoothing=0.05
    - AdamW + CosineAnnealingWarmRestarts
    -"""
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import DataLoader, TensorDataset
    from models.architectures.ensemble import EnsembleModel
    from config.settings import get_settings

    settings = get_settings()
    ml_cfg   = settings.ml
    device   = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    print(f"\n[3/5]   ...")
    unique, counts = np.unique(y, return_counts=True)
    total = len(y)
    weight_arr = np.zeros(3, dtype=np.float32)
    for cls, cnt in zip(unique, counts):
        weight_arr[cls] = total / (3 * cnt)
    class_weights = torch.tensor(weight_arr).to(device)
    print(f"   : BUY={weight_arr[0]:.2f} | "
          f"HOLD={weight_arr[1]:.2f} | SELL={weight_arr[2]:.2f}")

    # 시간순 분할 (증강 데이터는 뒤에 붙어있으므로 앞 80% 사용)
    split = int(len(X) * 0.8)
    X_tr, X_val = X[:split], X[split:]
    y_tr, y_val = y[:split], y[split:]
    print(f"  Train: {len(X_tr)}개 | Val: {len(X_val)}개")

    train_loader = DataLoader(
        TensorDataset(torch.tensor(X_tr), torch.tensor(y_tr)),
        batch_size=ml_cfg.batch_size, shuffle=True,
        pin_memory=True, num_workers=0
    )
    val_loader = DataLoader(
        TensorDataset(torch.tensor(X_val), torch.tensor(y_val)),
        batch_size=ml_cfg.batch_size, shuffle=False,
        pin_memory=True, num_workers=0
    )

    print(f"\n[4/5]    ({device})...")
    model = EnsembleModel(
        input_size  = ml_cfg.feature_count,
        hidden_size = ml_cfg.hidden_size,
        dropout     = ml_cfg.dropout,
        num_heads   = ml_cfg.attention_heads,
        seq_len     = ml_cfg.sequence_length,
    ).to(device)

    # CrossEntropyLoss + label_smoothing (v3의 FocalLoss 대신)
    criterion = nn.CrossEntropyLoss(
        weight=class_weights, label_smoothing=0.05
    )
    optimizer = optim.AdamW(
        model.parameters(),
        lr=ml_cfg.learning_rate,
        weight_decay=1e-4
    )
    # CosineAnnealingWarmRestarts: T_0=30 주기로 재시작
    scheduler = optim.lr_scheduler.CosineAnnealingWarmRestarts(
        optimizer, T_0=30, T_mult=1, eta_min=1e-5
    )

    use_amp = (device.type == "cuda")
    scaler  = torch.amp.GradScaler("cuda") if use_amp else None

    best_val_loss = float("inf")
    best_state    = None
    patience      = ml_cfg.early_stopping_patience  # 20
    no_improve    = 0
    best_epoch    = 0
    best_metrics  = {}

    for epoch in range(1, ml_cfg.epochs + 1):
        model.train()
        tr_loss = 0.0
        for Xb, yb in train_loader:
            Xb, yb = Xb.to(device), yb.to(device)
            optimizer.zero_grad()
            if use_amp:
                with torch.amp.autocast("cuda"):
                    out    = model(Xb)
                    logits = out[0] if isinstance(out, tuple) else out
                    loss   = criterion(logits, yb)
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(optimizer)
                scaler.update()
            else:
                out    = model(Xb)
                logits = out[0] if isinstance(out, tuple) else out
                loss   = criterion(logits, yb)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
            tr_loss += loss.item()
        scheduler.step()

        model.eval()
        val_loss = 0.0
        correct  = 0
        buy_correct = sell_correct = buy_total = sell_total = 0

        with torch.no_grad():
            for Xb, yb in val_loader:
                Xb, yb = Xb.to(device), yb.to(device)
                if use_amp:
                    with torch.amp.autocast("cuda"):
                        out    = model(Xb)
                        logits = out[0] if isinstance(out, tuple) else out
                        loss   = criterion(logits, yb)
                else:
                    out    = model(Xb)
                    logits = out[0] if isinstance(out, tuple) else out
                    loss   = criterion(logits, yb)
                val_loss += loss.item()
                preds = logits.argmax(dim=1)
                correct += (preds == yb).sum().item()
                mb = (yb == 0); ms = (yb == 2)
                if mb.sum() > 0:
                    buy_correct += (preds[mb] == 0).sum().item()
                    buy_total   += mb.sum().item()
                if ms.sum() > 0:
                    sell_correct += (preds[ms] == 2).sum().item()
                    sell_total   += ms.sum().item()

        avg_tr   = tr_loss  / len(train_loader)
        avg_val  = val_loss / len(val_loader)
        acc      = correct  / len(X_val) * 100
        buy_acc  = buy_correct  / buy_total  * 100 if buy_total  else 0
        sell_acc = sell_correct / sell_total * 100 if sell_total else 0

        if epoch % 10 == 0:
            lr_now = optimizer.param_groups[0]['lr']
            print(f"  Epoch {epoch:3d}/{ml_cfg.epochs} | "
                  f"Train={avg_tr:.4f} | Val={avg_val:.4f} | "
                  f"Acc={acc:.1f}% | BUY={buy_acc:.1f}% | "
                  f"SELL={sell_acc:.1f}% | LR={lr_now:.6f}")

        if avg_val < best_val_loss:
            best_val_loss = avg_val
            best_state    = {k: v.cpu().clone()
                             for k, v in model.state_dict().items()}
            best_epoch    = epoch
            no_improve    = 0
            best_metrics  = {
                "best_val_loss": best_val_loss,
                "best_epoch":    best_epoch,
                "final_val_acc": acc,
                "buy_acc":       buy_acc,
                "sell_acc":      sell_acc,
            }
        else:
            no_improve += 1
            if no_improve >= patience:
                print(f"\n  ⏹  Early Stopping "
                      f"(epoch {epoch}, best={best_epoch})")
                break

    if best_state:
        model.load_state_dict(best_state)
    print(f"\n     | Best Val Loss={best_val_loss:.4f} "
          f"| Best Epoch={best_epoch}")
    return model, best_metrics


def save_v4(model, metrics, test_df):
    import torch, shutil
    from models.inference.predictor import MLPredictor

    src = Path("models/saved/ensemble_best.pt")
    if src.exists():
        shutil.copy(src, "models/saved/ensemble_backup_before_v4.pt")
        print("   v2  : ensemble_backup_before_v4.pt")

    Path("models/saved").mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), src)
    print(f"\n[5/5]  : models/saved/ensemble_best.pt")

    # v2 vs v4 비교
    v2_acc  = 60.7
    v4_acc  = metrics["final_val_acc"]
    v2_buy  = 54.4
    v4_buy  = metrics["buy_acc"]
    v2_sell = 55.6
    v4_sell = metrics["sell_acc"]

    print(f"\n   v2 vs v4  ")
    print(f"  Val Acc : v2={v2_acc:.1f}%  →  v4={v4_acc:.1f}%  "
          f"{' ' if v4_acc > v2_acc else ' '}")
    print(f"  BUY Acc : v2={v2_buy:.1f}%  →  v4={v4_buy:.1f}%  "
          f"{' ' if v4_buy > v2_buy else ' '}")
    print(f"  SELL Acc: v2={v2_sell:.1f}%  →  v4={v4_sell:.1f}%  "
          f"{' ' if v4_sell > v2_sell else ' '}")

    # v4가 나쁘면 자동 롤백
    if v4_acc < v2_acc - 5.0:
        print("\n    v4  v2 5%   →  ")
        shutil.copy("models/saved/ensemble_backup_before_v4.pt", src)
        print("   v2   ")
        return False

    print("\n   ...")
    try:
        predictor = MLPredictor()
        predictor.load_model()
        result = predictor.predict("KRW-BTC", test_df.tail(80))
        if result:
            print(f"   ={result.get('signal')} | "
                  f"신뢰도={result.get('confidence',0):.2%} | "
                  f"BUY={result.get('buy_prob',0):.2%} | "
                  f"SELL={result.get('sell_prob',0):.2%}")
    except Exception as e:
        print(f"      : {e}")
    return True


async def main():
    print("=" * 62)
    print("  APEX BOT — ML   v4")
    print("  v3  : CE Loss +  + WarmRestart LR")
    print("=" * 62)

    combined, all_dfs = await collect_data_v4()
    if combined is None:
        print("   "); return

    print("\n[2/5]   v4 (threshold=1.0%, horizon=5)...")
    from models.train.trainer import ModelTrainer
    trainer = ModelTrainer()

    loop = asyncio.get_event_loop()
    try:
        X, y = await loop.run_in_executor(
            None,
            lambda: extract_features_v4(
                trainer, combined, threshold=0.010, horizon=5
            )
        )
    except Exception as e:
        import traceback
        print(f"   : {e}")
        traceback.print_exc()
        return

    print(f"  X shape: {X.shape}  Y shape: {y.shape}")
    if len(X) < 200:
        print(f"  : {len(X)}개"); return

    try:
        model, metrics = await loop.run_in_executor(
            None, lambda: train_v4(trainer, X, y)
        )
    except Exception as e:
        import traceback
        print(f"  : {e}")
        traceback.print_exc()
        return

    success = await loop.run_in_executor(
        None, lambda: save_v4(model, metrics, all_dfs[0])
    )

    print("\n" + "=" * 62)
    if success:
        print(" ML v4  ! v2  ")
    else:
        print("  ML v4   → v2  ")
    print(f"\n  Val Acc  : {metrics['final_val_acc']:.1f}%")
    print(f"  BUY Acc  : {metrics['buy_acc']:.1f}%")
    print(f"  SELL Acc : {metrics['sell_acc']:.1f}%")
    print(f"  Best Epoch: {metrics['best_epoch']}")
    print("\n : python start_paper.py")
    print("=" * 62)


if __name__ == "__main__":
    asyncio.run(main())
