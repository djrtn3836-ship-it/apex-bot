"""
APEX BOT - ML 모델 추론기
RTX 5060 CUDA + AMP (FP16) 실시간 추론
BiLSTM(30%) + TFT(40%) + CNN-LSTM(30%) 앙상블

수정 이력:
  v1.1 - EnsembleModel.forward() 튜플 반환 방어적 언팩 추가
         proba가 tuple 전체로 남는 버그 → softmax AttributeError 수정
       - 이중 tuple 중첩 처리 추가
       - AMP autocast 디바이스 타입 명시
"""
import os
import time
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any
import numpy as np
import pandas as pd
from loguru import logger

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    TORCH_OK = True
except ImportError:
    TORCH_OK = False
    logger.warning("PyTorch 미설치 - ML 추론 비활성화")

from config.settings import get_settings
# CombinedSignal import 제거 (predict()는 dict 반환)


def _safe_unpack_model_output(raw_output: Any) -> Tuple[Any, Any]:
    """
    ✅ FIX: EnsembleModel.forward() 반환값 안전 언팩
    가능한 형태:
      - (proba_tensor, details_dict)       ← 정상
      - ((proba_tensor, details_dict), _)  ← 이중 tuple
      - proba_tensor                       ← tensor만
    """
    if isinstance(raw_output, tuple):
        if len(raw_output) == 2:
            first, second = raw_output
            # 첫 번째 원소가 또 tuple인 경우 (이중 중첩)
            if isinstance(first, tuple):
                return first[0], first[1] if len(first) > 1 else {}
            # 정상: (tensor, dict) 또는 (tensor, tensor)
            return first, second
        elif len(raw_output) == 1:
            return raw_output[0], {}
    # tuple이 아닌 경우 tensor만 반환된 것
    return raw_output, {}


class MLPredictor:
    """
    실시간 ML 앙상블 추론기

    입력: 처리된 OHLCV + 지표 DataFrame (최근 60 캔들)
    출력: {signal, confidence, buy_prob, sell_prob, model_agreement, inference_ms}
    """

    CLASS_NAMES    = ["BUY", "HOLD", "SELL"]
    MIN_CONFIDENCE = 0.42  # lowered: model outputs 0.42-0.46 range
    SEQ_LEN        = 60

    def __init__(self):
        self.settings = get_settings()
        self.device   = self._get_device()
        self._model   = None
        self._is_loaded = False
        self._inference_times: List[float] = []
        self._is_fp16: bool = False   # FP16 모드 플래그

        logger.info(f"✅ MLPredictor 초기화 | 디바이스: {self.device}")

    # ── 디바이스 ─────────────────────────────────────────────────

    def _get_device(self) -> str:
        if TORCH_OK and self.settings.ml.use_gpu:
            if torch.cuda.is_available():
                return "cuda"
        return "cpu"

    # ── 모델 로드 ────────────────────────────────────────────────

    def load_model(self) -> bool:
        if not TORCH_OK:
            return False
        try:
            from models.architectures.ensemble import EnsembleModel

            model_path = (
                Path(self.settings.ml.model_save_dir) / "ensemble_best.pt"
            )

            self._model = EnsembleModel(
                input_size=self.settings.ml.feature_count,
                hidden_size=self.settings.ml.hidden_size,
                num_heads=self.settings.ml.attention_heads,
                seq_len=self.SEQ_LEN,
                dropout=0.0,
            )

            if model_path.exists():
                state = torch.load(str(model_path), map_location=self.device, weights_only=True)
                self._model.load_state_dict(state)
                logger.info(f"✅ 앙상블 모델 로드: {model_path}")
            else:
                logger.warning("저장된 모델 없음 → 신규 초기화 모델 사용")

            self._model.to(self.device)
            self._model.eval()
            # ✅ FP32 유지 + AMP autocast로 FP16 연산 처리
            # (aot_eager 컴파일러와 dtype 충돌 방지)
            self._is_fp16 = False
            if self.device == "cuda":
                logger.info("⚡ AMP autocast 활성화 (FP32 입력 → FP16 연산, dtype 충돌 방지)")
            self._is_loaded = True
            return True

        except Exception as e:
            logger.error(f"모델 로드 실패: {e}")
            self._is_loaded = False
            return False

    # ── 단일 예측 ────────────────────────────────────────────────

    def predict(self, market: str, df: pd.DataFrame) -> Optional[Dict]:
        """단일 마켓 실시간 예측"""
        if not self._is_loaded or not TORCH_OK:
            return {"signal": "HOLD", "confidence": 0.0, "buy_prob": 0.0, "hold_prob": 1.0, "sell_prob": 0.0, "model_agreement": 0.0}
        if df is None or len(df) < self.SEQ_LEN:
            return {"signal": "HOLD", "confidence": 0.0, "buy_prob": 0.0, "hold_prob": 1.0, "sell_prob": 0.0, "model_agreement": 0.0}

        try:
            t_start = time.perf_counter()

            X = self._extract_features(df)
            if X is None:
                return {"signal": "HOLD", "confidence": 0.0, "buy_prob": 0.0, "hold_prob": 1.0, "sell_prob": 0.0, "model_agreement": 0.0}

            tensor = torch.FloatTensor(X).unsqueeze(0).to(self.device)
            # ✅ 입력은 FP32 유지 (aot_eager는 FP32로 트레이스됨)
            # 모델 내부 AMP autocast가 FP16 연산 처리

            with torch.inference_mode():
                with torch.amp.autocast(
                    device_type=self.device,
                    enabled=(self.device == "cuda"),
                ):
                    raw_output = self._model(tensor)

            # ✅ FIX: 안전 언팩
            proba_raw, details = _safe_unpack_model_output(raw_output)

            # proba_raw가 tensor인지 확인
            if not isinstance(proba_raw, torch.Tensor):
                logger.error(
                    f"예측 출력 타입 오류: {type(proba_raw)} — "
                    f"torch.Tensor 필요"
                )
                return {"signal": "HOLD", "confidence": 0.0, "buy_prob": 0.0, "hold_prob": 1.0, "sell_prob": 0.0, "model_agreement": 0.0}

            proba   = F.softmax(proba_raw, dim=-1)
            proba_np = proba.cpu().float().numpy()[0]

            signal_idx = int(proba_np.argmax())
            confidence = float(proba_np.max())

            # 모델 동의율
            agreement = self._calc_agreement(details)

            elapsed = (time.perf_counter() - t_start) * 1000
            self._inference_times.append(elapsed)
            if len(self._inference_times) > 100:
                self._inference_times = self._inference_times[-100:]

            signal = self.CLASS_NAMES[signal_idx]
            if confidence < self.MIN_CONFIDENCE:
                signal = "HOLD"

            result = {
                "market":          market,
                "signal":          signal,
                "confidence":      confidence,
                "buy_prob":        float(proba_np[0]),
                "hold_prob":       float(proba_np[1]),
                "sell_prob":       float(proba_np[2]),
                "model_agreement": agreement,
                "inference_ms":    elapsed,
            }

            logger.debug(
                f"ML 추론 | {market} | {signal} | "
                f"신뢰도={confidence:.2f} | 동의율={agreement:.2f} | "
                f"{elapsed:.1f}ms"
            )
            return result

        except Exception as e:
            logger.error(f"ML 추론 오류 ({market}): {e}")
            return {"signal": "HOLD", "confidence": 0.0, "buy_prob": 0.0, "hold_prob": 1.0, "sell_prob": 0.0, "model_agreement": 0.0}

    # ── 배치 예측 ────────────────────────────────────────────────

    def predict_batch(
        self, market_df_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, Dict]:
        """다중 코인 배치 추론 (GPU 병렬)"""
        if not self._is_loaded or not TORCH_OK:
            return {}

        valid_markets, tensors = [], []
        for market, df in market_df_map.items():
            X = self._extract_features(df)
            if X is not None:
                tensors.append(X)
                valid_markets.append(market)

        if not tensors:
            return {}

        results = {}
        try:
            batch = torch.FloatTensor(np.array(tensors)).to(self.device)
            # ✅ 입력은 FP32 유지 (aot_eager는 FP32로 트레이스됨)

            with torch.inference_mode():
                with torch.amp.autocast(
                    device_type=self.device,
                    enabled=(self.device == "cuda"),
                ):
                    raw_output = self._model(batch)

            # ✅ FIX: 안전 언팩
            proba_raw, details = _safe_unpack_model_output(raw_output)

            if not isinstance(proba_raw, torch.Tensor):
                return {}

            proba    = F.softmax(proba_raw, dim=-1)
            proba_np = proba.cpu().float().numpy()

            for i, market in enumerate(valid_markets):
                p          = proba_np[i]
                idx        = int(p.argmax())
                confidence = float(p.max())
                agreement  = self._calc_agreement_batch(details, i)
                signal     = self.CLASS_NAMES[idx]
                if confidence < self.MIN_CONFIDENCE:
                    signal = "HOLD"

                results[market] = {
                    "signal":          signal,
                    "confidence":      confidence,
                    "buy_prob":        float(p[0]),
                    "hold_prob":       float(p[1]),
                    "sell_prob":       float(p[2]),
                    "model_agreement": agreement,
                }

        except Exception as e:
            logger.error(f"배치 추론 오류: {e}")

        return results

    # ── 모델 동의율 계산 ─────────────────────────────────────────

    def _calc_agreement(self, details: Dict) -> float:
        """3모델 예측 일치도 (단일 샘플)"""
        if not details:
            return 1.0
        try:
            sigs = []
            for key in ("bilstm", "tft", "cnn_lstm"):
                if key in details and details[key] is not None:
                    sigs.append(int(details[key][0].argmax().item()))
            if not sigs:
                return 1.0
            most_common = max(set(sigs), key=sigs.count)
            return sigs.count(most_common) / len(sigs)
        except Exception:
            return 1.0

    def _calc_agreement_batch(self, details: Dict, idx: int) -> float:
        """3모델 예측 일치도 (배치 i번째)"""
        if not details:
            return 1.0
        try:
            sigs = []
            for key in ("bilstm", "tft", "cnn_lstm"):
                if key in details and details[key] is not None:
                    d = details[key]
                    if idx < len(d):
                        sigs.append(int(d[idx].argmax().item()))
            if not sigs:
                return 1.0
            most_common = max(set(sigs), key=sigs.count)
            return sigs.count(most_common) / len(sigs)
        except Exception:
            return 1.0

    # ── 피처 추출 ────────────────────────────────────────────────

    def _extract_features(self, df: pd.DataFrame) -> Optional[np.ndarray]:
        """DataFrame → 정규화된 피처 배열 (SEQ_LEN, feature_count)"""
        required = [
            "open", "high", "low", "close", "volume",
            "ema20", "ema50", "ema200", "rsi", "macd", "macd_signal",
            "macd_hist", "bb_upper", "bb_mid", "bb_lower", "bb_pct",
            "bb_width", "atr", "atr_pct", "stoch_k", "stoch_d", "vwap",
            "adx", "di_plus", "di_minus", "obv", "cci", "mfi",
            "vol_ratio", "supertrend_dir",
        ]
        available = [c for c in required if c in df.columns]
        if len(available) < 10:
            return None

        try:
            data = df[available].tail(self.SEQ_LEN).values.astype(np.float32)
            if len(data) < self.SEQ_LEN:
                pad  = np.zeros(
                    (self.SEQ_LEN - len(data), data.shape[1]), dtype=np.float32
                )
                data = np.vstack([pad, data])

            # Z-score 정규화
            mean = data.mean(axis=0, keepdims=True)
            std  = data.std(axis=0,  keepdims=True) + 1e-8
            data = (data - mean) / std

            # feature_count 맞춤
            target = self.settings.ml.feature_count
            if data.shape[1] < target:
                pad  = np.zeros((self.SEQ_LEN, target - data.shape[1]), dtype=np.float32)
                data = np.hstack([data, pad])
            else:
                data = data[:, :target]

            data = np.nan_to_num(data, nan=0.0, posinf=1.0, neginf=-1.0)
            return data

        except Exception as e:
            logger.error(f"피처 추출 오류: {e}")
            return None

    # ── 재학습 트리거 ────────────────────────────────────────────

    def retrain(self):
        logger.info("🔄 ML 재학습 트리거")
        try:
            from models.train.trainer import ModelTrainer
            ModelTrainer().train_all_markets()
        except Exception as e:
            logger.error(f"재학습 실패: {e}")

    # ── 통계 ──────────────────────────────────────────────────────

    def get_inference_stats(self) -> Dict:
        if not self._inference_times:
            return {}
        t = self._inference_times
        return {
            "avg_ms":           float(np.mean(t)),
            "p95_ms":           float(np.percentile(t, 95)),
            "max_ms":           float(np.max(t)),
            "total_inferences": len(t),
            "device":           self.device,
        }
