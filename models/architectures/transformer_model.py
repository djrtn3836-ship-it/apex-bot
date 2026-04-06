"""
APEX BOT - Temporal Fusion Transformer (TFT) 모델
RTX 5060 CUDA 가속 + FP16 혼합 정밀도
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Tuple, Optional
from loguru import logger


class MultiHeadAttention(nn.Module):
    """멀티헤드 어텐션 (CUDA 최적화)"""

    def __init__(self, d_model: int, num_heads: int, dropout: float = 0.1):
        super().__init__()
        assert d_model % num_heads == 0
        self.d_model = d_model
        self.num_heads = num_heads
        self.d_k = d_model // num_heads

        self.W_q = nn.Linear(d_model, d_model, bias=False)
        self.W_k = nn.Linear(d_model, d_model, bias=False)
        self.W_v = nn.Linear(d_model, d_model, bias=False)
        self.W_o = nn.Linear(d_model, d_model)
        self.dropout = nn.Dropout(dropout)
        self.scale = self.d_k ** -0.5

    def forward(self, q: torch.Tensor, k: torch.Tensor, v: torch.Tensor,
                mask: Optional[torch.Tensor] = None) -> torch.Tensor:
        B, T, _ = q.shape

        Q = self.W_q(q).view(B, T, self.num_heads, self.d_k).transpose(1, 2)
        K = self.W_k(k).view(B, -1, self.num_heads, self.d_k).transpose(1, 2)
        V = self.W_v(v).view(B, -1, self.num_heads, self.d_k).transpose(1, 2)

        scores = torch.matmul(Q, K.transpose(-2, -1)) * self.scale
        if mask is not None:
            scores = scores.masked_fill(mask == 0, -1e9)

        attn = self.dropout(F.softmax(scores, dim=-1))
        out = torch.matmul(attn, V)
        out = out.transpose(1, 2).contiguous().view(B, T, self.d_model)
        return self.W_o(out)


class VariableSelectionNetwork(nn.Module):
    """
    TFT 변수 선택 네트워크
    - 입력 피처의 중요도를 동적으로 가중
    """
    def __init__(self, input_size: int, hidden_size: int, dropout: float = 0.1):
        super().__init__()
        self.gating = nn.Sequential(
            nn.Linear(input_size, hidden_size),
            nn.ELU(),
            nn.Linear(hidden_size, input_size),
            nn.Softmax(dim=-1),
        )
        self.transform = nn.Linear(input_size, hidden_size)
        self.dropout = nn.Dropout(dropout)
        self.norm = nn.LayerNorm(hidden_size)

    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        weights = self.gating(x)
        selected = x * weights
        out = self.dropout(F.elu(self.transform(selected)))
        return self.norm(out), weights


class GatedResidualNetwork(nn.Module):
    """Gated Residual Network (TFT 핵심 블록)"""

    def __init__(self, input_size: int, hidden_size: int, output_size: int,
                 dropout: float = 0.1, context_size: Optional[int] = None):
        super().__init__()
        self.fc1 = nn.Linear(input_size, hidden_size)
        self.fc2 = nn.Linear(hidden_size, output_size * 2)  # GLU 게이팅

        if context_size is not None:
            self.context_proj = nn.Linear(context_size, hidden_size, bias=False)
        else:
            self.context_proj = None

        self.residual = nn.Linear(input_size, output_size) if input_size != output_size else None
        self.norm = nn.LayerNorm(output_size)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor,
                context: Optional[torch.Tensor] = None) -> torch.Tensor:
        h = F.elu(self.fc1(x))
        if context is not None and self.context_proj is not None:
            h = h + self.context_proj(context)

        out = self.fc2(self.dropout(h))
        out, gate = out.chunk(2, dim=-1)
        out = out * torch.sigmoid(gate)  # Gated Linear Unit

        residual = self.residual(x) if self.residual else x
        return self.norm(out + residual)


class TemporalFusionTransformer(nn.Module):
    """
    Temporal Fusion Transformer (TFT)
    - 단기/장기 의존성 동시 포착
    - 피처 중요도 해석 가능
    - RTX 5060 CUDA 최적화 (FP16)

    입력: (Batch, Seq_len, Features)
    출력: (Batch, 3) - [BUY확률, HOLD확률, SELL확률]
    """

    def __init__(self, input_size: int = 120, hidden_size: int = 256,
                 num_heads: int = 8, num_layers: int = 4,
                 dropout: float = 0.2, seq_len: int = 60):
        super().__init__()
        self.hidden_size = hidden_size
        self.seq_len = seq_len

        # 입력 임베딩
        self.input_proj = nn.Linear(input_size, hidden_size)

        # 변수 선택
        self.var_selection = VariableSelectionNetwork(hidden_size, hidden_size, dropout)

        # LSTM 인코더 (단기 패턴)
        self.lstm_encoder = nn.LSTM(
            hidden_size, hidden_size, num_layers=2,
            batch_first=True, dropout=dropout, bidirectional=False
        )

        # Transformer 디코더 (장기 패턴)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=hidden_size, nhead=num_heads,
            dim_feedforward=hidden_size * 4,
            dropout=dropout, batch_first=True,
            norm_first=True  # Pre-LN (학습 안정성)
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers, enable_nested_tensor=False)

        # Gated Residual Networks
        self.grn_layers = nn.ModuleList([
            GatedResidualNetwork(hidden_size, hidden_size, hidden_size, dropout)
            for _ in range(3)
        ])

        # 어텐션 (해석 가능성)
        self.interpretable_attn = MultiHeadAttention(hidden_size, num_heads, dropout)

        # 출력 헤드
        self.output_norm = nn.LayerNorm(hidden_size)
        self.classifier = nn.Sequential(
            GatedResidualNetwork(hidden_size, hidden_size // 2, hidden_size // 2, dropout),
            nn.Linear(hidden_size // 2, 3),  # BUY / HOLD / SELL
        )

        self._init_weights()
        logger.info(f"✅ TFT 모델 초기화 | 파라미터: {self._count_params():,}개")

    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Args:
            x: (B, T, F) - 배치, 시퀀스, 피처
        Returns:
            logits: (B, 3) - 분류 로짓
            attn_weights: (B, T) - 해석 가능한 어텐션 가중치
        """
        B, T, _ = x.shape

        # 입력 임베딩
        h = self.input_proj(x)  # (B, T, H)

        # 변수 선택
        h, var_weights = self.var_selection(h)  # (B, T, H)

        # LSTM 인코더
        lstm_out, _ = self.lstm_encoder(h)  # (B, T, H)

        # Transformer
        tf_out = self.transformer(lstm_out)  # (B, T, H)

        # GRN 레이어
        for grn in self.grn_layers:
            tf_out = grn(tf_out)

        # 해석 가능한 어텐션
        ctx = tf_out[:, -1:, :]  # 마지막 스텝
        attn_out = self.interpretable_attn(ctx, tf_out, tf_out)
        attn_weights = attn_out.squeeze(1)

        # 출력
        out = self.output_norm(attn_weights + tf_out[:, -1, :])
        logits = self.classifier(out)

        return logits, var_weights.mean(dim=1)

    def predict_proba(self, x: torch.Tensor) -> torch.Tensor:
        """소프트맥스 확률 반환"""
        with torch.no_grad():
            logits, _ = self.forward(x)
            return F.softmax(logits, dim=-1)

    def _init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, nn.LSTM):
                for name, param in m.named_parameters():
                    if "weight" in name:
                        nn.init.orthogonal_(param)
                    elif "bias" in name:
                        nn.init.zeros_(param)

    def _count_params(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


class CNNLSTMModel(nn.Module):
    """
    CNN-LSTM 하이브리드 모델
    - CNN: 국부 패턴 추출 (차트 패턴)
    - LSTM: 시계열 의존성
    """

    def __init__(self, input_size: int = 120, hidden_size: int = 128,
                 seq_len: int = 60, dropout: float = 0.2):
        super().__init__()

        # CNN 피처 추출
        self.cnn = nn.Sequential(
            nn.Conv1d(input_size, 64, kernel_size=3, padding=1),
            nn.BatchNorm1d(64),
            nn.GELU(),
            nn.Conv1d(64, 128, kernel_size=5, padding=2),
            nn.BatchNorm1d(128),
            nn.GELU(),
            nn.Conv1d(128, 256, kernel_size=7, padding=3),
            nn.BatchNorm1d(256),
            nn.GELU(),
            nn.Dropout(dropout),
        )

        # LSTM
        self.lstm = nn.LSTM(256, hidden_size, num_layers=2,
                            batch_first=True, dropout=dropout, bidirectional=True)

        # 출력
        self.classifier = nn.Sequential(
            nn.Linear(hidden_size * 2, 64),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(64, 3),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, T, F) → CNN: (B, F, T)
        h = self.cnn(x.transpose(1, 2))
        h = h.transpose(1, 2)  # → (B, T, C)
        lstm_out, (hidden, _) = self.lstm(h)
        # 양방향 마지막 은닉층
        out = torch.cat([hidden[-2], hidden[-1]], dim=1)
        return self.classifier(out)

    def predict_proba(self, x: torch.Tensor) -> torch.Tensor:
        with torch.no_grad():
            logits = self.forward(x)
            return F.softmax(logits, dim=-1)
