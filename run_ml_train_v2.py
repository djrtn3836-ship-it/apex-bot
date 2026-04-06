"""
run_ml_train_v2.py
개선된 ML 학습:
  - 500개 캔들 (기존 200 → 500, 약 3주치)
  - 클래스 불균형 class_weight 적용
  - threshold 완화 (1.5% → 0.8%) → BUY/SELL 샘플 증가
  - 학습 결과 상세 리포트
실행: python run_ml_train_v2.py
"""
import asyncio, sys, numpy as np
from pathlib import Path

ROOT = Path(".")
sys.path.insert(0, str(ROOT))


async def collect_data():
    from config.settings import get_settings
    from data.collectors.rest_collector import RestCollector
    from data.processors.candle_processor import CandleProcessor
    import pandas as pd

    settings  = get_settings()
    markets   = settings.trading.target_markets
    collector = RestCollector()
    processor = CandleProcessor()
    all_dfs   = []

    print(f"\n[1/5] OHLCV 수집 (500캔들 × {len(markets)}코인)...")
    for market in markets:
        try:
            df = await collector.get_ohlcv(market, "minute60", 500)
            if df is None or len(df) < 100:
                print(f"  ⚠️  {market}: 데이터 부족 — 스킵")
                continue
            df_proc = await processor.process(market, df, "60")
            if df_proc is None or len(df_proc) < 100:
                print(f"  ⚠️  {market}: 전처리 실패 — 스킵")
                continue
            all_dfs.append(df_proc)
            print(f"  ✅ {market}: {len(df_proc)}행")
            await asyncio.sleep(0.35)
        except Exception as e:
            print(f"  ❌ {market}: {e}")

    if not all_dfs:
        return None, None

    import pandas as pd
    combined = pd.concat(all_dfs, ignore_index=True)
    print(f"\n  📊 합산: {len(combined)}행 × {len(combined.columns)}컬럼")
    return combined, all_dfs


def extract_with_balanced_labels(trainer, df, threshold=0.008):
    """threshold 완화로 BUY/SELL 샘플 증가"""
    import numpy as np
    from config.settings import get_settings

    settings = get_settings()
    seq_len  = settings.ml.sequence_length  # 60

    feature_cols = [c for c in [
        "open", "high", "low", "close", "volume",
        "ema20", "ema50", "ema200", "rsi", "macd", "macd_signal", "macd_hist",
        "bb_upper", "bb_mid", "bb_lower", "bb_pct", "bb_width",
        "atr", "atr_pct", "stoch_k", "stoch_d", "vwap",
        "adx", "di_plus", "di_minus", "obv", "cci", "mfi",
        "vol_ratio", "supertrend_dir",
    ] if c in df.columns]

    if len(feature_cols) < 5:
        raise ValueError(f"피처 부족: {len(feature_cols)}개")

    # threshold 완화한 레이블 생성
    horizon = settings.ml.prediction_horizon  # 5
    close   = df["close"].values
    n       = len(close)
    labels  = np.ones(n, dtype=int)  # HOLD

    buy_cnt = sell_cnt = hold_cnt = 0
    for i in range(n - horizon):
        future_return = (close[i + horizon] - close[i]) / close[i]
        if future_return > threshold:
            labels[i] = 0   # BUY
            buy_cnt += 1
        elif future_return < -threshold:
            labels[i] = 2   # SELL
            sell_cnt += 1
        else:
            hold_cnt += 1

    print(f"\n  레이블 분포 (threshold={threshold*100:.1f}%):")
    print(f"    BUY : {buy_cnt:4d}개 ({buy_cnt/n*100:.1f}%)")
    print(f"    HOLD: {hold_cnt:4d}개 ({hold_cnt/n*100:.1f}%)")
    print(f"    SELL: {sell_cnt:4d}개 ({sell_cnt/n*100:.1f}%)")

    # 피처 정규화
    data = df[feature_cols].values.astype(np.float32)
    data_norm = trainer._rolling_normalize(data)
    data_norm = np.nan_to_num(data_norm, nan=0, posinf=1, neginf=-1)

    target_f = settings.ml.feature_count  # 120
    if data_norm.shape[1] < target_f:
        pad = np.zeros((len(data_norm), target_f - data_norm.shape[1]))
        data_norm = np.hstack([data_norm, pad])
    else:
        data_norm = data_norm[:, :target_f]

    # 시퀀스 생성
    X, y = [], []
    for i in range(seq_len, len(data_norm) - horizon):
        X.append(data_norm[i - seq_len:i])
        y.append(labels[i])

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int64)


def train_with_class_weight(trainer, X, y):
    """class_weight 적용 학습"""
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import DataLoader, TensorDataset
    from models.architectures.ensemble import EnsembleModel
    from config.settings import get_settings

    settings   = get_settings()
    ml_cfg     = settings.ml
    device     = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    print(f"\n[3/5] 클래스 가중치 계산...")

    # 클래스 불균형 보정 가중치
    unique, counts = np.unique(y, return_counts=True)
    total  = len(y)
    # 역빈도 가중치: 적은 클래스에 높은 가중치
    weight_arr = np.zeros(3, dtype=np.float32)
    for cls, cnt in zip(unique, counts):
        weight_arr[cls] = total / (3 * cnt)

    print(f"  클래스 가중치: BUY={weight_arr[0]:.2f} | "
          f"HOLD={weight_arr[1]:.2f} | SELL={weight_arr[2]:.2f}")

    class_weights = torch.tensor(weight_arr).to(device)

    # Train/Val 분할 (시간순)
    split = int(len(X) * 0.8)
    X_tr, X_val = X[:split], X[split:]
    y_tr, y_val = y[:split], y[split:]

    print(f"  Train: {len(X_tr)}개 | Val: {len(X_val)}개")

    # 데이터로더
    train_ds = TensorDataset(
        torch.tensor(X_tr), torch.tensor(y_tr)
    )
    val_ds = TensorDataset(
        torch.tensor(X_val), torch.tensor(y_val)
    )
    train_loader = DataLoader(train_ds, batch_size=ml_cfg.batch_size,
                              shuffle=True)
    val_loader   = DataLoader(val_ds,   batch_size=ml_cfg.batch_size,
                              shuffle=False)

    # 모델 생성
    print(f"\n[4/5] 모델 학습 시작 ({device})...")
    model = EnsembleModel(
        input_size  = ml_cfg.feature_count,
        hidden_size = ml_cfg.hidden_size,
        dropout     = ml_cfg.dropout,
        num_heads   = ml_cfg.attention_heads,
        seq_len     = ml_cfg.sequence_length,
    ).to(device)

    # class_weight 적용 손실함수
    criterion = nn.CrossEntropyLoss(weight=class_weights)
    optimizer = optim.AdamW(model.parameters(),
                            lr=ml_cfg.learning_rate, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=ml_cfg.epochs
    )

    # AMP (FP16)
    use_amp = (device.type == "cuda")
    scaler  = torch.amp.GradScaler("cuda") if use_amp else None

    best_val_loss = float("inf")
    best_state    = None
    patience      = ml_cfg.early_stopping_patience  # 20
    no_improve    = 0
    best_epoch    = 0

    for epoch in range(1, ml_cfg.epochs + 1):
        # ── Train ──
        model.train()
        tr_loss = 0.0
        for Xb, yb in train_loader:
            Xb, yb = Xb.to(device), yb.to(device)
            optimizer.zero_grad()
            if use_amp:
                with torch.amp.autocast("cuda"):
                    out  = model(Xb)
                    logits = out[0] if isinstance(out, tuple) else out
                    loss = criterion(logits, yb)
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

        # ── Validation ──
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
                # BUY/SELL 개별 정확도
                for cls, bc, bt in [(0, "buy", "buy_t"), (2, "sell", "sell_t")]:
                    mask = (yb == cls)
                    if mask.sum() > 0:
                        if cls == 0:
                            buy_correct += (preds[mask] == cls).sum().item()
                            buy_total   += mask.sum().item()
                        else:
                            sell_correct += (preds[mask] == cls).sum().item()
                            sell_total   += mask.sum().item()

        avg_tr  = tr_loss  / len(train_loader)
        avg_val = val_loss / len(val_loader)
        acc     = correct  / len(X_val) * 100
        buy_acc  = buy_correct  / buy_total  * 100 if buy_total  else 0
        sell_acc = sell_correct / sell_total * 100 if sell_total else 0

        if epoch % 10 == 0:
            print(f"  Epoch {epoch:3d}/{ml_cfg.epochs} | "
                  f"Train={avg_tr:.4f} | Val={avg_val:.4f} | "
                  f"Acc={acc:.1f}% | "
                  f"BUY={buy_acc:.1f}% | SELL={sell_acc:.1f}%")

        # Early Stopping
        if avg_val < best_val_loss:
            best_val_loss = avg_val
            best_state    = {k: v.cpu().clone()
                             for k, v in model.state_dict().items()}
            best_epoch    = epoch
            no_improve    = 0
        else:
            no_improve += 1
            if no_improve >= patience:
                print(f"\n  ⏹  Early Stopping (epoch {epoch}, "
                      f"best={best_epoch})")
                break

    # 최적 가중치 복원
    if best_state:
        model.load_state_dict(best_state)

    print(f"\n  ✅ 학습 완료 | Best Val Loss={best_val_loss:.4f} "
          f"| Best Epoch={best_epoch}")

    return model, {
        "best_val_loss":  best_val_loss,
        "best_epoch":     best_epoch,
        "final_val_acc":  acc,
        "buy_acc":        buy_acc,
        "sell_acc":       sell_acc,
    }


def save_and_test(model, test_df):
    import torch
    from models.inference.predictor import MLPredictor

    save_dir = Path("models/saved")
    save_dir.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), save_dir / "ensemble_best.pt")
    print(f"\n[5/5] 모델 저장 완료: models/saved/ensemble_best.pt")

    # 예측 테스트
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
            print(f"     동의율: {result.get('model_agreement', 0):.2%}")
    except Exception as e:
        print(f"  ⚠️  예측 테스트 오류: {e}")


async def main():
    print("=" * 62)
    print("  APEX BOT — ML 개선 학습 v2")
    print("  변경: 500캔들 + class_weight + threshold 0.8%")
    print("=" * 62)

    # 1. 데이터 수집
    combined, all_dfs = await collect_data()
    if combined is None:
        print("❌ 데이터 수집 실패"); return

    # 2. 피처 추출 (개선된 threshold)
    print("\n[2/5] 피처 추출 (threshold=0.8%)...")
    from models.train.trainer import ModelTrainer
    trainer = ModelTrainer()

    loop = asyncio.get_event_loop()
    try:
        X, y = await loop.run_in_executor(
            None,
            lambda: extract_with_balanced_labels(trainer, combined, threshold=0.008)
        )
    except Exception as e:
        print(f"❌ 피처 추출 실패: {e}"); return

    print(f"  X shape: {X.shape}  Y shape: {y.shape}")

    if len(X) < 100:
        print(f"❌ 샘플 부족: {len(X)}개"); return

    # 3. 클래스 가중치 적용 학습
    try:
        model, metrics = await loop.run_in_executor(
            None,
            lambda: train_with_class_weight(trainer, X, y)
        )
    except Exception as e:
        import traceback
        print(f"❌ 학습 실패: {e}")
        traceback.print_exc()
        return

    # 4. 저장 + 테스트
    await loop.run_in_executor(
        None,
        lambda: save_and_test(model, all_dfs[0])
    )

    print("\n" + "=" * 62)
    print("🎉 ML 개선 학습 완료!")
    print()
    print(f"  Val Loss : {metrics['best_val_loss']:.4f}")
    print(f"  Val Acc  : {metrics['final_val_acc']:.1f}%")
    print(f"  BUY Acc  : {metrics['buy_acc']:.1f}%")
    print(f"  SELL Acc : {metrics['sell_acc']:.1f}%")
    print(f"  Best Epoch: {metrics['best_epoch']}")
    print()
    print("봇 재시작:")
    print("  python start_paper.py")
    print("=" * 62)


if __name__ == "__main__":
    asyncio.run(main())
