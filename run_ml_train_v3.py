# run_ml_train_v3.py
"""
APEX BOT — ML 개선 학습 v3
개선 사항:
  1. 캔들 500 → 1000개 (데이터 2배)
  2. 레이블 threshold 0.8% → 0.6% (BUY/SELL 샘플 증가)
  3. prediction horizon 5 → 3 (더 빠른 반응)
  4. 피처 추가: fear_greed_proxy, trend_strength, volatility_regime
  5. Label Smoothing 적용 (과적합 방지)
  6. Focal Loss 적용 (어려운 샘플에 집중)
  7. 학습률 Warmup 추가
실행: python run_ml_train_v3.py
"""
import asyncio, sys, numpy as np
from pathlib import Path

ROOT = Path(".")
sys.path.insert(0, str(ROOT))


async def collect_data_v3():
    """1000캔들 수집 (기존 500 → 1000)"""
    from config.settings import get_settings
    from data.collectors.rest_collector import RestCollector
    from data.processors.candle_processor import CandleProcessor
    import pandas as pd

    settings  = get_settings()
    markets   = settings.trading.target_markets
    collector = RestCollector()
    processor = CandleProcessor()
    all_dfs   = []

    print(f"\n[1/5] OHLCV 수집 (1000캔들 × {len(markets)}코인)...")
    for market in markets:
        try:
            # 1000캔들 수집 (Upbit 최대 200개 → 5회 요청)
            import pandas as pd
            dfs = []
            for _ in range(5):
                df_part = await collector.get_ohlcv(market, "minute60", 200)
                if df_part is not None and len(df_part) > 0:
                    dfs.append(df_part)
                await asyncio.sleep(0.35)

            if not dfs:
                print(f"  ⚠️  {market}: 데이터 없음")
                continue

            df = pd.concat(dfs).drop_duplicates().sort_index()
            df_proc = await processor.process(market, df, "60")
            if df_proc is None or len(df_proc) < 100:
                print(f"  ⚠️  {market}: 전처리 실패")
                continue

            all_dfs.append(df_proc)
            print(f"  ✅ {market}: {len(df_proc)}행")
        except Exception as e:
            print(f"  ❌ {market}: {e}")

    if not all_dfs:
        return None, None

    import pandas as pd
    combined = pd.concat(all_dfs, ignore_index=True)
    print(f"\n  📊 합산: {len(combined)}행 × {len(combined.columns)}컬럼")
    return combined, all_dfs


def extract_features_v3(trainer, df, threshold=0.006, horizon=3):
    """
    개선된 피처 추출
    - threshold 0.8% → 0.6%
    - horizon 5 → 3
    - 피처 추가: trend_strength, vol_regime, price_acceleration
    """
    import numpy as np
    from config.settings import get_settings

    settings = get_settings()
    seq_len  = settings.ml.sequence_length  # 60

    # 기본 피처 컬럼
    base_cols = [c for c in [
        "open", "high", "low", "close", "volume",
        "ema20", "ema50", "ema200", "rsi", "macd", "macd_signal", "macd_hist",
        "bb_upper", "bb_mid", "bb_lower", "bb_pct", "bb_width",
        "atr", "atr_pct", "stoch_k", "stoch_d", "vwap",
        "adx", "di_plus", "di_minus", "obv", "cci", "mfi",
        "vol_ratio", "supertrend_dir",
    ] if c in df.columns]

    if len(base_cols) < 5:
        raise ValueError(f"피처 부족: {len(base_cols)}개")

    data = df[base_cols].values.astype(np.float32)

    # ── 추가 피처 계산 ──────────────────────────────────────────
    close  = df["close"].values.astype(np.float32)
    volume = df["volume"].values.astype(np.float32)
    high   = df["high"].values.astype(np.float32)
    low    = df["low"].values.astype(np.float32)
    n      = len(close)

    extra = np.zeros((n, 10), dtype=np.float32)

    for i in range(20, n):
        # 1. 추세 강도 (EMA 기울기)
        if i >= 20:
            slope = (close[i] - close[i-20]) / (close[i-20] + 1e-8)
            extra[i, 0] = np.clip(slope, -0.5, 0.5)

        # 2. 가격 가속도 (2차 미분)
        if i >= 3:
            v1 = close[i-1] - close[i-2]
            v2 = close[i]   - close[i-1]
            extra[i, 1] = np.clip((v2 - v1) / (close[i] + 1e-8), -0.1, 0.1)

        # 3. 변동성 레짐 (ATR 대비 현재 범위)
        if i >= 14:
            atr = np.mean(high[i-14:i] - low[i-14:i])
            cur_range = high[i] - low[i]
            extra[i, 2] = np.clip(cur_range / (atr + 1e-8) - 1, -2, 2)

        # 4. 거래량 가속도
        if i >= 5:
            vol_ma5 = np.mean(volume[i-5:i])
            extra[i, 3] = np.clip(volume[i] / (vol_ma5 + 1e-8) - 1, -3, 3)

        # 5. 고점/저점 돌파 여부
        if i >= 20:
            extra[i, 4] = 1.0 if close[i] > np.max(high[i-20:i]) else 0.0
            extra[i, 5] = 1.0 if close[i] < np.min(low[i-20:i])  else 0.0

        # 6. 캔들 방향성 연속성
        if i >= 3:
            dirs = np.sign(np.diff(close[i-3:i+1]))
            extra[i, 6] = np.sum(dirs) / 3.0

        # 7. 거래량-가격 다이버전스
        if i >= 10:
            price_ret  = (close[i] - close[i-10]) / (close[i-10] + 1e-8)
            vol_change = (np.mean(volume[i-5:i]) - np.mean(volume[i-10:i-5])) / (np.mean(volume[i-10:i-5]) + 1e-8)
            extra[i, 7] = np.clip(price_ret - vol_change, -2, 2)

        # 8. 시간대 피처 (한국 거래량 많은 시간: 9~11시, 20~22시)
        if hasattr(df.index, '__len__') and i < len(df.index):
            try:
                hour = df.index[i].hour if hasattr(df.index[i], 'hour') else 0
                extra[i, 8] = 1.0 if hour in [9, 10, 11, 20, 21, 22] else 0.0
            except Exception:
                extra[i, 8] = 0.0

        # 9. 52주 고점 대비 위치
        if i >= 52:
            high52 = np.max(high[i-52:i])
            low52  = np.min(low[i-52:i])
            extra[i, 9] = (close[i] - low52) / (high52 - low52 + 1e-8)

    # 결합
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

    # ── 레이블 생성 (threshold=0.6%, horizon=3) ───────────────
    labels  = np.ones(n, dtype=int)  # HOLD
    buy_cnt = sell_cnt = hold_cnt = 0

    for i in range(n - horizon):
        future_return = (close[i + horizon] - close[i]) / (close[i] + 1e-8)
        if future_return > threshold:
            labels[i] = 0   # BUY
            buy_cnt += 1
        elif future_return < -threshold:
            labels[i] = 2   # SELL
            sell_cnt += 1
        else:
            hold_cnt += 1

    print(f"\n  레이블 분포 (threshold={threshold*100:.1f}%, horizon={horizon}):")
    print(f"    BUY : {buy_cnt:4d}개 ({buy_cnt/n*100:.1f}%)")
    print(f"    HOLD: {hold_cnt:4d}개 ({hold_cnt/n*100:.1f}%)")
    print(f"    SELL: {sell_cnt:4d}개 ({sell_cnt/n*100:.1f}%)")
    print(f"    추가 피처: {data_combined.shape[1]}개 (기본 {len(base_cols)} + 추가 10)")

    # 시퀀스 생성
    X, y = [], []
    for i in range(seq_len, len(data_norm) - horizon):
        X.append(data_norm[i - seq_len:i])
        y.append(labels[i])

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int64)


def train_v3(trainer, X, y):
    """
    개선된 학습
    - Focal Loss (어려운 샘플 집중)
    - Label Smoothing (과적합 방지)
    - Warmup + CosineAnnealing LR
    """
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import DataLoader, TensorDataset
    from models.architectures.ensemble import EnsembleModel
    from config.settings import get_settings

    settings = get_settings()
    ml_cfg   = settings.ml
    device   = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    print(f"\n[3/5] 클래스 가중치 + Focal Loss 계산...")

    # 클래스 가중치
    unique, counts = np.unique(y, return_counts=True)
    total = len(y)
    weight_arr = np.zeros(3, dtype=np.float32)
    for cls, cnt in zip(unique, counts):
        weight_arr[cls] = total / (3 * cnt)
    class_weights = torch.tensor(weight_arr).to(device)
    print(f"  클래스 가중치: BUY={weight_arr[0]:.2f} | "
          f"HOLD={weight_arr[1]:.2f} | SELL={weight_arr[2]:.2f}")

    # Train/Val 분할
    split = int(len(X) * 0.8)
    X_tr, X_val = X[:split], X[split:]
    y_tr, y_val = y[:split], y[split:]
    print(f"  Train: {len(X_tr)}개 | Val: {len(X_val)}개")

    train_loader = DataLoader(
        TensorDataset(torch.tensor(X_tr), torch.tensor(y_tr)),
        batch_size=ml_cfg.batch_size, shuffle=True
    )
    val_loader = DataLoader(
        TensorDataset(torch.tensor(X_val), torch.tensor(y_val)),
        batch_size=ml_cfg.batch_size, shuffle=False
    )

    # 모델
    print(f"\n[4/5] 모델 학습 시작 ({device})...")
    model = EnsembleModel(
        input_size  = ml_cfg.feature_count,
        hidden_size = ml_cfg.hidden_size,
        dropout     = ml_cfg.dropout,
        num_heads   = ml_cfg.attention_heads,
        seq_len     = ml_cfg.sequence_length,
    ).to(device)

    # Focal Loss (gamma=2 → 어려운 샘플에 집중)
    class FocalLoss(nn.Module):
        def __init__(self, weight, gamma=2.0, label_smoothing=0.1):
            super().__init__()
            self.weight = weight
            self.gamma  = gamma
            self.ls     = label_smoothing
            self.ce     = nn.CrossEntropyLoss(
                weight=weight, label_smoothing=label_smoothing, reduction='none'
            )
        def forward(self, logits, targets):
            ce_loss = self.ce(logits, targets)
            pt = torch.exp(-ce_loss)
            focal = ((1 - pt) ** self.gamma) * ce_loss
            return focal.mean()

    criterion = FocalLoss(weight=class_weights, gamma=2.0, label_smoothing=0.1)
    optimizer = optim.AdamW(
        model.parameters(), lr=ml_cfg.learning_rate, weight_decay=1e-4
    )

    # Warmup(10 에포크) + CosineAnnealing
    def warmup_cosine(epoch):
        warmup = 10
        if epoch < warmup:
            return epoch / warmup
        progress = (epoch - warmup) / (ml_cfg.epochs - warmup)
        return 0.5 * (1 + np.cos(np.pi * progress))

    scheduler = optim.lr_scheduler.LambdaLR(optimizer, lr_lambda=warmup_cosine)

    use_amp = (device.type == "cuda")
    scaler  = torch.amp.GradScaler("cuda") if use_amp else None

    best_val_loss = float("inf")
    best_state    = None
    patience      = ml_cfg.early_stopping_patience
    no_improve    = 0
    best_epoch    = 0
    best_metrics  = {}

    for epoch in range(1, ml_cfg.epochs + 1):
        # Train
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

        # Validation
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
                mask_buy  = (yb == 0)
                mask_sell = (yb == 2)
                if mask_buy.sum()  > 0:
                    buy_correct  += (preds[mask_buy]  == 0).sum().item()
                    buy_total    += mask_buy.sum().item()
                if mask_sell.sum() > 0:
                    sell_correct += (preds[mask_sell] == 2).sum().item()
                    sell_total   += mask_sell.sum().item()

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
                print(f"\n  ⏹  Early Stopping (epoch {epoch}, best={best_epoch})")
                break

    if best_state:
        model.load_state_dict(best_state)
    print(f"\n  ✅ 학습 완료 | Best Val Loss={best_val_loss:.4f} "
          f"| Best Epoch={best_epoch}")
    return model, best_metrics


def save_and_test_v3(model, test_df):
    import torch
    from models.inference.predictor import MLPredictor

    # 기존 모델 백업
    src  = Path("models/saved/ensemble_best.pt")
    bak  = Path("models/saved/ensemble_backup_v2.pt")
    if src.exists():
        import shutil
        shutil.copy(src, bak)
        print(f"  📦 기존 모델 백업: {bak}")

    save_dir = Path("models/saved")
    save_dir.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), save_dir / "ensemble_best.pt")
    print(f"\n[5/5] 모델 저장 완료: models/saved/ensemble_best.pt")

    print("\n  예측 테스트...")
    try:
        predictor = MLPredictor()
        predictor.load_model()
        result = predictor.predict("KRW-BTC", test_df.tail(80))
        if result:
            print(f"  ✅ 예측 성공")
            print(f"     신호:   {result.get('signal')}")
            print(f"     신뢰도: {result.get('confidence', 0):.2%}")
            print(f"     BUY:   {result.get('buy_prob', 0):.2%}  "
                  f"HOLD: {result.get('hold_prob', 0):.2%}  "
                  f"SELL: {result.get('sell_prob', 0):.2%}")
    except Exception as e:
        print(f"  ⚠️  예측 테스트 오류: {e}")


async def main():
    print("=" * 62)
    print("  APEX BOT — ML 개선 학습 v3")
    print("  1000캔들 + FocalLoss + LabelSmoothing + Warmup LR")
    print("=" * 62)

    combined, all_dfs = await collect_data_v3()
    if combined is None:
        print("❌ 데이터 수집 실패"); return

    print("\n[2/5] 피처 추출 v3 (threshold=0.6%, horizon=3)...")
    from models.train.trainer import ModelTrainer
    trainer = ModelTrainer()

    loop = asyncio.get_event_loop()
    try:
        X, y = await loop.run_in_executor(
            None,
            lambda: extract_features_v3(trainer, combined, threshold=0.006, horizon=3)
        )
    except Exception as e:
        import traceback
        print(f"❌ 피처 추출 실패: {e}")
        traceback.print_exc()
        return

    print(f"  X shape: {X.shape}  Y shape: {y.shape}")
    if len(X) < 200:
        print(f"❌ 샘플 부족: {len(X)}개"); return

    try:
        model, metrics = await loop.run_in_executor(
            None, lambda: train_v3(trainer, X, y)
        )
    except Exception as e:
        import traceback
        print(f"❌ 학습 실패: {e}")
        traceback.print_exc()
        return

    await loop.run_in_executor(
        None, lambda: save_and_test_v3(model, all_dfs[0])
    )

    print("\n" + "=" * 62)
    print("🎉 ML v3 학습 완료!")
    print()
    print(f"  Val Loss  : {metrics['best_val_loss']:.4f}")
    print(f"  Val Acc   : {metrics['final_val_acc']:.1f}%")
    print(f"  BUY Acc   : {metrics['buy_acc']:.1f}%")
    print(f"  SELL Acc  : {metrics['sell_acc']:.1f}%")
    print(f"  Best Epoch: {metrics['best_epoch']}")
    print()

    # v2 대비 개선 여부
    print("  v2 대비 변경:")
    print("  ✅ 데이터: 500 → 최대 1000캔들")
    print("  ✅ threshold: 0.8% → 0.6%")
    print("  ✅ horizon: 5 → 3봉")
    print("  ✅ FocalLoss(gamma=2) 적용")
    print("  ✅ LabelSmoothing(0.1) 적용")
    print("  ✅ Warmup(10) + CosineAnnealing LR")
    print("  ✅ 추가 피처 10개")
    print()
    print("봇 재시작:")
    print("  python start_paper.py")
    print("=" * 62)


if __name__ == "__main__":
    asyncio.run(main())
