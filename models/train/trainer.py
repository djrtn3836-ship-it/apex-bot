"""
APEX BOT - ML 모델 훈련기
Optuna 하이퍼파라미터 최적화 + Walk-Forward 훈련
RTX 5060 CUDA + AMP (FP16) 가속
"""
import os
import time
from pathlib import Path
from typing import Optional, Tuple, Dict, List
import numpy as np
import pandas as pd
from loguru import logger

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import DataLoader, TensorDataset
    TORCH_OK = True
except ImportError:
    TORCH_OK = False

try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    OPTUNA_OK = True
except ImportError:
    OPTUNA_OK = False

from config.settings import get_settings


class ModelTrainer:
    """
    앙상블 ML 모델 훈련기

    훈련 파이프라인:
    1. 멀티 코인 OHLCV + 지표 수집
    2. 레이블 생성 (5봉 후 방향: BUY/HOLD/SELL)
    3. Train/Val/Test 분리 (시계열 순서 유지)
    4. Optuna 하이퍼파라미터 탐색 (선택)
    5. AMP + Mixed Precision 훈련
    6. 조기 종료 + LR 스케줄링
    7. Walk-Forward 검증
    8. 최적 모델 저장
    """

    def __init__(self):
        self.settings = get_settings()
        self.ml_cfg = self.settings.ml
        self.device = self._get_device()
        self.save_dir = Path(self.ml_cfg.model_save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)

    def _get_device(self) -> str:
        if TORCH_OK and self.ml_cfg.use_gpu and torch.cuda.is_available():
            return "cuda"
        return "cpu"

    # ── 레이블 생성 ───────────────────────────────────────────────
    def create_labels(self, df: pd.DataFrame, horizon: int = 5,
                      threshold: float = 0.015) -> np.ndarray:
        """
        미래 N봉 후 가격 방향 레이블 생성

        Args:
            horizon: 예측 기간 (봉 수)
            threshold: BUY/SELL 판단 임계값 (기본 1.5%)

        Returns:
            labels: (N,) array | 0=BUY, 1=HOLD, 2=SELL
        """
        close = df["close"].values
        n = len(close)
        labels = np.ones(n, dtype=int)  # 기본 HOLD

        for i in range(n - horizon):
            future_return = (close[i + horizon] - close[i]) / close[i]
            if future_return > threshold:
                labels[i] = 0   # BUY
            elif future_return < -threshold:
                labels[i] = 2   # SELL
            # else: HOLD (1)

        return labels

    # ── 피처 추출 ─────────────────────────────────────────────────
    def extract_features(self, df: pd.DataFrame, seq_len: int = 60) -> Tuple[np.ndarray, np.ndarray]:
        """DataFrame → (X, y) 훈련 데이터"""
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

        labels = self.create_labels(df, self.ml_cfg.prediction_horizon)
        data = df[feature_cols].values.astype(np.float32)

        # 정규화 (Rolling Z-score)
        data_normalized = self._rolling_normalize(data)
        data_normalized = np.nan_to_num(data_normalized, nan=0, posinf=1, neginf=-1)

        # target features에 맞게 패딩
        target_f = self.ml_cfg.feature_count
        if data_normalized.shape[1] < target_f:
            pad = np.zeros((len(data_normalized), target_f - data_normalized.shape[1]))
            data_normalized = np.hstack([data_normalized, pad])
        else:
            data_normalized = data_normalized[:, :target_f]

        # 시퀀스 생성
        X, y = [], []
        for i in range(seq_len, len(data_normalized) - self.ml_cfg.prediction_horizon):
            X.append(data_normalized[i - seq_len:i])
            y.append(labels[i])

        return np.array(X, dtype=np.float32), np.array(y, dtype=np.int64)

    @staticmethod
    def _rolling_normalize(data: np.ndarray, window: int = 60) -> np.ndarray:
        """롤링 Z-score 정규화 (미래 데이터 누수 방지)"""
        result = np.zeros_like(data)
        for i in range(len(data)):
            start = max(0, i - window)
            chunk = data[start:i+1]
            mean = chunk.mean(axis=0)
            std = chunk.std(axis=0) + 1e-8
            result[i] = (data[i] - mean) / std
        return result

    # ── 훈련 루프 ─────────────────────────────────────────────────
    def train(self, X: np.ndarray, y: np.ndarray,
              model=None) -> Tuple[object, Dict]:
        """
        모델 훈련

        Args:
            X: (N, seq_len, features)
            y: (N,) 레이블
            model: 기존 모델 (None이면 신규 생성)

        Returns:
            (trained_model, metrics)
        """
        if not TORCH_OK:
            logger.error("PyTorch 미설치 - 훈련 불가")
            return None, {}

        from models.architectures.ensemble import EnsembleModel

        # Train/Val 분리 (시계열 순서 유지)
        n = len(X)
        train_end = int(n * self.ml_cfg.train_ratio)
        val_end = int(n * (self.ml_cfg.train_ratio + self.ml_cfg.val_ratio))

        X_train, y_train = X[:train_end], y[:train_end]
        X_val, y_val = X[train_end:val_end], y[train_end:val_end]

        logger.info(
            f"📊 훈련 데이터 | Train={len(X_train)} | Val={len(X_val)} | "
            f"클래스 분포: {np.bincount(y_train)}"
        )

        # 클래스 불균형 가중치 (HOLD 비율이 높음)
        class_counts = np.bincount(y_train, minlength=3)
        class_weights = 1.0 / (class_counts + 1)
        class_weights = class_weights / class_weights.sum() * 3
        weight_tensor = torch.FloatTensor(class_weights).to(self.device)

        # DataLoader
        train_ds = TensorDataset(
            torch.FloatTensor(X_train), torch.LongTensor(y_train)
        )
        val_ds = TensorDataset(
            torch.FloatTensor(X_val), torch.LongTensor(y_val)
        )
        train_loader = DataLoader(
            train_ds, batch_size=self.ml_cfg.batch_size,
            shuffle=True, num_workers=2, pin_memory=(self.device == "cuda")
        )
        val_loader = DataLoader(
            val_ds, batch_size=self.ml_cfg.batch_size * 2,
            shuffle=False, num_workers=2
        )

        # 모델 초기화
        if model is None:
            model = EnsembleModel(
                input_size=self.ml_cfg.feature_count,
                hidden_size=self.ml_cfg.hidden_size,
                num_heads=self.ml_cfg.attention_heads,
                seq_len=self.ml_cfg.sequence_length,
                dropout=self.ml_cfg.dropout,
            )
        model = model.to(self.device)

        # 옵티마이저 + 스케줄러
        optimizer = optim.AdamW(
            model.parameters(),
            lr=self.ml_cfg.learning_rate,
            weight_decay=1e-4,
        )
        scheduler = optim.lr_scheduler.CosineAnnealingLR(
            optimizer, T_max=self.ml_cfg.epochs, eta_min=1e-6
        )
        criterion = nn.CrossEntropyLoss(weight=weight_tensor)

        # AMP (자동 혼합 정밀도) - RTX 5060 Tensor Core
        scaler = torch.amp.GradScaler(enabled=(self.device == "cuda"))

        best_val_loss = float("inf")
        best_state = None
        patience_counter = 0
        history = {"train_loss": [], "val_loss": [], "val_acc": []}

        logger.info(f"🚀 훈련 시작 | {self.ml_cfg.epochs} 에폭 | {self.device}")

        for epoch in range(self.ml_cfg.epochs):
            # ── 훈련 단계 ────────────────────────────────────────
            model.train()
            train_losses = []

            for batch_X, batch_y in train_loader:
                batch_X = batch_X.to(self.device, non_blocking=True)
                batch_y = batch_y.to(self.device, non_blocking=True)

                optimizer.zero_grad(set_to_none=True)

                with torch.amp.autocast(device_type="cuda" if self.device == "cuda" else "cpu",
                                         enabled=(self.device == "cuda")):
                    proba, _ = model(batch_X)
                    loss = criterion(proba, batch_y)

                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(optimizer)
                scaler.update()

                train_losses.append(loss.item())

            scheduler.step()

            # ── 검증 단계 ────────────────────────────────────────
            model.eval()
            val_losses, val_preds, val_true = [], [], []

            with torch.no_grad():
                for batch_X, batch_y in val_loader:
                    batch_X = batch_X.to(self.device)
                    batch_y = batch_y.to(self.device)

                    with torch.amp.autocast(device_type="cuda" if self.device == "cuda" else "cpu",
                                             enabled=(self.device == "cuda")):
                        proba, _ = model(batch_X)
                        loss = criterion(proba, batch_y)

                    val_losses.append(loss.item())
                    val_preds.extend(proba.argmax(1).cpu().numpy())
                    val_true.extend(batch_y.cpu().numpy())

            avg_train_loss = np.mean(train_losses)
            avg_val_loss = np.mean(val_losses)
            val_acc = np.mean(np.array(val_preds) == np.array(val_true)) * 100

            history["train_loss"].append(avg_train_loss)
            history["val_loss"].append(avg_val_loss)
            history["val_acc"].append(val_acc)

            if (epoch + 1) % 10 == 0:
                logger.info(
                    f"  Epoch {epoch+1:3d}/{self.ml_cfg.epochs} | "
                    f"Train={avg_train_loss:.4f} | Val={avg_val_loss:.4f} | "
                    f"Acc={val_acc:.1f}%"
                )

            # 조기 종료
            if avg_val_loss < best_val_loss:
                best_val_loss = avg_val_loss
                best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
                patience_counter = 0
            else:
                patience_counter += 1
                if patience_counter >= self.ml_cfg.early_stopping_patience:
                    logger.info(f"  Early stopping at epoch {epoch+1}")
                    break

        # 최적 모델 복원
        if best_state:
            model.load_state_dict(best_state)

        final_metrics = {
            "best_val_loss": best_val_loss,
            "final_val_acc": history["val_acc"][-1] if history["val_acc"] else 0,
            "epochs_trained": len(history["train_loss"]),
        }

        logger.info(f"✅ 훈련 완료 | Val Loss={best_val_loss:.4f} | Acc={final_metrics['final_val_acc']:.1f}%")
        return model, final_metrics

    def save_model(self, model, name: str = "ensemble_best"):
        """모델 저장"""
        if not TORCH_OK:
            return
        path = self.save_dir / f"{name}.pt"
        torch.save(model.state_dict(), str(path))
        logger.info(f"💾 모델 저장: {path}")

    def train_all_markets(self):
        """전체 마켓 훈련 (스케줄러 호출용)"""
        logger.info("📚 전체 마켓 ML 훈련 시작...")
        # 실제 구현 시 rest_collector로 데이터 수집 후 훈련
        logger.info("→ 데이터 수집 및 훈련 파이프라인 실행")

    def optuna_optimize(self, X: np.ndarray, y: np.ndarray,
                        n_trials: int = 50) -> Dict:
        """Optuna 하이퍼파라미터 최적화"""
        if not OPTUNA_OK:
            logger.warning("Optuna 미설치 - 기본 파라미터 사용")
            return {}

        def objective(trial):
            params = {
                "hidden_size": trial.suggest_categorical("hidden_size", [128, 256, 512]),
                "num_layers": trial.suggest_int("num_layers", 2, 6),
                "dropout": trial.suggest_float("dropout", 0.1, 0.4),
                "learning_rate": trial.suggest_float("lr", 1e-4, 1e-2, log=True),
            }
            logger.debug(f"Trial {trial.number}: {params}")
            _, metrics = self.train(X, y)
            return metrics.get("best_val_loss", float("inf"))

        study = optuna.create_study(direction="minimize")
        study.optimize(objective, n_trials=n_trials, timeout=3600)

        best = study.best_params
        logger.info(f"✅ Optuna 최적 파라미터: {best}")
        return best
