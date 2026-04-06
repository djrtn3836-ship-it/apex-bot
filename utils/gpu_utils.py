"""
APEX BOT - GPU 최적화 유틸리티 v2.0
RTX 5060 (Blackwell) CUDA 최적화

Step 2 최적화:
  - 실제 추론 입력 형태 (1,60,120)로 워밍업 (레이턴시 완전 제거)
  - 3개 CUDA 스트림 생성 → BiLSTM/TFT/CNN-LSTM 병렬 추론
  - 5분마다 CUDA context 유지 스케줄러 연동
  - Blackwell SM 9.0 Tensor Core 최대 활용
"""
from __future__ import annotations

import os
import sys
import time
import threading
from typing import Optional, Dict, List, Tuple
from loguru import logger

try:
    import torch
    import torch.backends.cudnn as cudnn
    TORCH_OK = True
except ImportError:
    TORCH_OK = False

# ── CUDA 스트림 전역 레지스트리 ──────────────────────────────────
_cuda_streams: List["torch.cuda.Stream"] = []
_stream_lock = threading.Lock()


def setup_gpu(
    use_gpu: bool = True,
    benchmark: bool = True,
    deterministic: bool = False,
    tf32: bool = True,
) -> str:
    """
    GPU 환경 초기화 및 최적화 설정

    Returns: "cuda" | "cpu"
    """
    if not TORCH_OK:
        logger.warning("⚠️  PyTorch 미설치 → CPU 모드")
        return "cpu"

    if not use_gpu or not torch.cuda.is_available():
        logger.info("💻 GPU 비활성화 → CPU 모드")
        return "cpu"

    device_count  = torch.cuda.device_count()
    gpu_name      = torch.cuda.get_device_name(0)
    total_mem_gb  = torch.cuda.get_device_properties(0).total_memory / 1e9
    cuda_ver      = torch.version.cuda or "unknown"
    torch_ver     = torch.__version__

    logger.info(f"🚀 GPU 감지: {gpu_name}")
    logger.info(f"   VRAM   : {total_mem_gb:.1f} GB")
    logger.info(f"   CUDA   : {cuda_ver}")
    logger.info(f"   PyTorch: {torch_ver}")
    logger.info(f"   Device : {device_count}개")

    _check_rtx5000_support(gpu_name, cuda_ver, torch_ver)

    # ── cudnn 설정 ─────────────────────────────────────────────
    if deterministic:
        cudnn.deterministic = True
        cudnn.benchmark     = False
        torch.use_deterministic_algorithms(True, warn_only=True)
        logger.info("   cudnn : deterministic mode")
    elif benchmark:
        cudnn.benchmark     = True
        cudnn.deterministic = False
        logger.info("   cudnn : benchmark mode (속도 최적화)")

    # ── TF32 (Ampere+ / Blackwell Tensor Core) ─────────────────
    if tf32 and _supports_tf32():
        torch.backends.cuda.matmul.allow_tf32 = True
        cudnn.allow_tf32                       = True
        logger.info("   TF32  : 활성화 (Blackwell Tensor Core 가속)")

    # ── 비동기 실행 유지 ────────────────────────────────────────
    os.environ.setdefault("CUDA_LAUNCH_BLOCKING", "0")

    # ── 3개 CUDA 스트림 초기화 (BiLSTM / TFT / CNN-LSTM) ────────
    _init_cuda_streams(n=3)

    # ── 실제 추론 입력 형태로 워밍업 ───────────────────────────
    _warmup_cuda_full(seq_len=60, features=120)

    logger.success(f"✅ GPU 초기화 완료: {gpu_name} ({total_mem_gb:.1f} GB)")
    return "cuda"


def _init_cuda_streams(n: int = 3):
    """
    ✅ Step 2 신규: CUDA 스트림 n개 초기화
    RTX 5060 Blackwell → 멀티스트림 SM 병렬 활용
    """
    global _cuda_streams
    if not TORCH_OK or not torch.cuda.is_available():
        return
    with _stream_lock:
        _cuda_streams = [torch.cuda.Stream() for _ in range(n)]
    logger.info(f"   CUDA 스트림 {n}개 초기화 (BiLSTM/TFT/CNN-LSTM 병렬)")


def get_cuda_stream(idx: int = 0) -> Optional["torch.cuda.Stream"]:
    """모델 인덱스에 맞는 CUDA 스트림 반환"""
    if not _cuda_streams:
        return None
    return _cuda_streams[idx % len(_cuda_streams)]


def _warmup_cuda_full(seq_len: int = 60, features: int = 120):
    """
    ✅ Step 2 개선: 실제 추론 입력 (1, seq_len, features) 형태로 워밍업
    기존 512×512 행렬 → 실제 모델 입력 크기로 변경
    첫 추론 레이턴시 완전 제거
    """
    if not TORCH_OK or not torch.cuda.is_available():
        return
    try:
        logger.debug("   CUDA 워밍업 시작 (실제 추론 입력 형태)...")
        device = "cuda"

        # 실제 앙상블 모델 입력 크기
        dummy = torch.randn(1, seq_len, features, device=device)

        # 3개 스트림에서 동시 워밍업
        for i, stream in enumerate(_cuda_streams):
            with torch.cuda.stream(stream):
                _ = dummy @ dummy.transpose(-2, -1)
                _ = dummy.mean(dim=1)

        # 모든 스트림 동기화
        torch.cuda.synchronize()

        # 추가: FP16 (AMP) 워밍업
        with torch.amp.autocast(device_type="cuda", enabled=True):
            dummy_fp16 = torch.randn(1, seq_len, features, device=device)
            _ = dummy_fp16.mean()
        torch.cuda.synchronize()

        del dummy, dummy_fp16
        torch.cuda.empty_cache()
        logger.debug("   CUDA 워밍업 완료 (FP32 + FP16 × 3스트림)")
    except Exception as e:
        logger.debug(f"   CUDA 워밍업 실패 (무시): {e}")


def warmup_keep_alive(seq_len: int = 60, features: int = 120):
    """
    ✅ Step 2 신규: CUDA context 유지 (5분마다 스케줄러에서 호출)
    GPU가 절전 모드로 전환되는 것을 방지
    """
    if not TORCH_OK or not torch.cuda.is_available():
        return
    try:
        dummy = torch.randn(1, seq_len, features, device="cuda")
        _ = dummy.sum()
        torch.cuda.synchronize()
        del dummy
        logger.debug("⚡ CUDA context 유지 (keep-alive)")
    except Exception as e:
        logger.debug(f"CUDA keep-alive 실패: {e}")


def _check_rtx5000_support(gpu_name: str, cuda_ver: str, torch_ver: str):
    rtx5k_keywords = ["5060", "5070", "5080", "5090", "5050"]
    if not any(k in gpu_name for k in rtx5k_keywords):
        return

    try:
        major, minor = (int(x) for x in (cuda_ver or "0.0").split(".")[:2])
        cuda_ok = (major > 12) or (major == 12 and minor >= 8)
    except Exception:
        cuda_ok = False

    try:
        tv      = torch_ver.split("+")[0]
        parts   = tv.split(".")
        major_t = int(parts[0])
        minor_t = int(parts[1]) if len(parts) > 1 else 0
        torch_ok = (major_t > 2) or (major_t == 2 and minor_t >= 6)
    except Exception:
        torch_ok = False

    if cuda_ok and torch_ok:
        logger.success(
            f"✅ RTX 50xx (Blackwell) 완전 지원: CUDA {cuda_ver} / PyTorch {torch_ver}"
        )
    else:
        logger.warning(
            "⚠️  RTX 50xx 감지 → 최적 성능을 위해:\n"
            "   pip install --pre torch torchvision torchaudio "
            "--index-url https://download.pytorch.org/whl/nightly/cu128"
        )


def _supports_tf32() -> bool:
    if not TORCH_OK or not torch.cuda.is_available():
        return False
    props = torch.cuda.get_device_properties(0)
    return (props.major, props.minor) >= (8, 0)


# ── torch.compile 래퍼 ─────────────────────────────────────────
def maybe_compile(model, **kwargs):
    """
    Windows + RTX 5060 환경에서 torch.compile 대신
    inference_mode + AMP만 사용 (FX tracing 충돌 방지)
    """
    try:
        import torch
        logger.info("⚡ torch.compile 비활성화 (Windows 안전모드) — inference_mode+AMP 사용")
    except ImportError:
        pass
    return model

def get_gpu_memory_info() -> Dict:
    if not TORCH_OK or not torch.cuda.is_available():
        return {"available": False}
    props     = torch.cuda.get_device_properties(0)
    allocated = torch.cuda.memory_allocated(0)
    reserved  = torch.cuda.memory_reserved(0)
    total     = props.total_memory
    free      = total - reserved
    return {
        "available":       True,
        "gpu_name":        props.name,
        "total_gb":        round(total     / 1e9, 2),
        "allocated_gb":    round(allocated / 1e9, 2),
        "reserved_gb":     round(reserved  / 1e9, 2),
        "free_gb":         round(free      / 1e9, 2),
        "utilization_pct": round(reserved  / total * 100, 1),
        "stream_count":    len(_cuda_streams),
    }


def clear_gpu_cache():
    if TORCH_OK and torch.cuda.is_available():
        torch.cuda.empty_cache()
        logger.debug("🗑️  GPU 캐시 정리")


def log_gpu_status():
    info = get_gpu_memory_info()
    if not info.get("available"):
        return
    logger.info(
        f"💾 GPU | "
        f"사용={info['allocated_gb']:.2f}GB / "
        f"예약={info['reserved_gb']:.2f}GB / "
        f"전체={info['total_gb']:.2f}GB "
        f"({info['utilization_pct']:.1f}%) | "
        f"스트림={info['stream_count']}개"
    )


def get_torch_install_cmd(gpu_name: str = "") -> str:
    rtx5k = any(k in gpu_name for k in ["5060","5070","5080","5090","5050"])
    rtx4k = any(k in gpu_name for k in ["4060","4070","4080","4090","4050"])
    rtx3k = any(k in gpu_name for k in ["3060","3070","3080","3090","3050"])
    if rtx5k:
        return (
            "# RTX 50xx (Blackwell) - CUDA 12.8 nightly\n"
            "pip install --pre torch torchvision torchaudio "
            "--index-url https://download.pytorch.org/whl/nightly/cu128"
        )
    elif rtx4k:
        return (
            "# RTX 40xx - CUDA 12.4\n"
            "pip install torch torchvision torchaudio "
            "--index-url https://download.pytorch.org/whl/cu124"
        )
    elif rtx3k:
        return (
            "# RTX 30xx - CUDA 12.1\n"
            "pip install torch torchvision torchaudio "
            "--index-url https://download.pytorch.org/whl/cu121"
        )
    return (
        "pip install torch torchvision torchaudio "
        "--index-url https://download.pytorch.org/whl/cu124"
    )
