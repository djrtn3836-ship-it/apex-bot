"""
APEX BOT - Bidirectional LSTM 모델
RTX 5060 CUDA 최적화
가격 방향 예측 (BUY / HOLD / SELL 3클래스 분류)
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class AttentionLayer(nn.Module):
    """Self-Attention 레이어 (시퀀스 내 중요 타임스텝 강조)"""

    def __init__(self, hidden_size: int):
        super().__init__()
        self.attention = nn.Sequential(
            nn.Linear(hidden_size, hidden_size // 2),
            nn.Tanh(),
            nn.Linear(hidden_size // 2, 1),
        )

    def forward(self, lstm_output: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Args:
            lstm_output: (batch, seq_len, hidden_size)
        Returns:
            context: (batch, hidden_size)
            attention_weights: (batch, seq_len)
        """
        scores = self.attention(lstm_output).squeeze(-1)     # (batch, seq_len)
        weights = F.softmax(scores, dim=1)                   # 정규화
        context = torch.bmm(weights.unsqueeze(1), lstm_output).squeeze(1)  # (batch, hidden_size)
        return context, weights


class BiLSTMPredictor(nn.Module):
    """
    Bidirectional LSTM + Attention 가격 방향 예측 모델
    
    아키텍처:
    입력 → LayerNorm → Bi-LSTM × 4레이어 → Attention → FC → Softmax
    
    RTX 5060 최적화:
    - Mixed Precision (FP16)
    - Dropout으로 과적합 방지
    - Residual Connection
    """

    def __init__(
        self,
        input_size: int = 120,      # 피처 수
        hidden_size: int = 256,     # LSTM 은닉층 크기
        num_layers: int = 4,        # LSTM 레이어 수
        num_classes: int = 3,       # BUY(0) / HOLD(1) / SELL(2)
        dropout: float = 0.2,
        bidirectional: bool = True,
    ):
        super().__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.bidirectional = bidirectional
        self.num_directions = 2 if bidirectional else 1

        # 입력 정규화
        self.input_norm = nn.LayerNorm(input_size)

        # Bi-LSTM 스택
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0,
            bidirectional=bidirectional,
        )

        lstm_output_size = hidden_size * self.num_directions

        # Attention
        self.attention = AttentionLayer(lstm_output_size)

        # 출력 레이어
        self.fc = nn.Sequential(
            nn.Linear(lstm_output_size, hidden_size),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size, hidden_size // 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size // 2, num_classes),
        )

        # 가중치 초기화
        self._init_weights()

    def _init_weights(self):
        for name, param in self.named_parameters():
            if "weight_ih" in name:
                nn.init.xavier_uniform_(param)
            elif "weight_hh" in name:
                nn.init.orthogonal_(param)
            elif "bias" in name:
                nn.init.zeros_(param)

    def forward(
        self,
        x: torch.Tensor,
        return_attention: bool = False
    ) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        """
        Args:
            x: (batch_size, seq_len, input_size)
            return_attention: Attention 가중치 반환 여부
        Returns:
            logits: (batch_size, num_classes)
            attention_weights: Optional[(batch_size, seq_len)]
        """
        # 입력 정규화
        x = self.input_norm(x)

        # LSTM 처리
        lstm_out, _ = self.lstm(x)     # (batch, seq_len, hidden*2)

        # Attention
        context, attn_weights = self.attention(lstm_out)   # (batch, hidden*2)

        # 분류
        logits = self.fc(context)      # (batch, num_classes)

        if return_attention:
            return logits, attn_weights
        return logits, None

    def predict_proba(self, x: torch.Tensor) -> torch.Tensor:
        """확률 예측 (Softmax 적용)"""
        with torch.no_grad():
            logits, _ = self.forward(x)
            return F.softmax(logits, dim=-1)

    @property
    def num_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


class CNNLSTMPredictor(nn.Module):
    """
    CNN-LSTM 하이브리드 모델
    CNN: 로컬 패턴 추출 (캔들 패턴 감지)
    LSTM: 시간적 의존성 학습
    """

    def __init__(
        self,
        input_size: int = 120,
        seq_len: int = 60,
        hidden_size: int = 128,
        num_classes: int = 3,
        dropout: float = 0.2,
    ):
        super().__init__()

        # CNN 블록 (로컬 패턴 추출)
        self.cnn = nn.Sequential(
            nn.Conv1d(input_size, 64, kernel_size=3, padding=1),
            nn.BatchNorm1d(64),
            nn.GELU(),
            nn.Conv1d(64, 128, kernel_size=5, padding=2),
            nn.BatchNorm1d(128),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Conv1d(128, 64, kernel_size=3, padding=1),
            nn.BatchNorm1d(64),
            nn.GELU(),
        )

        # LSTM 블록
        self.lstm = nn.LSTM(
            input_size=64,
            hidden_size=hidden_size,
            num_layers=2,
            batch_first=True,
            dropout=dropout,
            bidirectional=True,
        )

        # 출력 레이어
        self.fc = nn.Sequential(
            nn.Linear(hidden_size * 2, 64),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(64, num_classes),
        )

    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, None]:
        """
        Args:
            x: (batch, seq_len, input_size)
        """
        # CNN: (batch, seq_len, input_size) → (batch, input_size, seq_len)
        x_cnn = x.permute(0, 2, 1)
        cnn_out = self.cnn(x_cnn)
        # → (batch, seq_len, 64)
        cnn_out = cnn_out.permute(0, 2, 1)

        # LSTM
        lstm_out, _ = self.lstm(cnn_out)
        # 마지막 타임스텝 사용
        last_hidden = lstm_out[:, -1, :]

        logits = self.fc(last_hidden)
        return logits, None


def build_model(model_type: str = "bilstm", **kwargs) -> nn.Module:
    """모델 팩토리 함수"""
    model_map = {
        "bilstm": BiLSTMPredictor,
        "cnn_lstm": CNNLSTMPredictor,
    }
    cls = model_map.get(model_type, BiLSTMPredictor)
    model = cls(**kwargs)
    logger.info(f"✅ 모델 생성: {model_type} | 파라미터: {model.num_parameters:,}개")
    return model

# 하위호환 별칭
BiLSTMModel = BiLSTMPredictor
CNNLSTMModel = CNNLSTMPredictor
