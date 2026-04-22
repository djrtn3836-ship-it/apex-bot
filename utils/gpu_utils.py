"""
APEX BOT - GPU Optimization Utility v2.0
RTX 5060 (Blackwell) CUDA optimization.
"""
from __future__ import annotations

import os
import threading
from typing import Optional, Dict, List

from loguru import logger

try:
    import torch
    import torch.backends.cudnn as cudnn
    TORCH_OK = True
except ImportError:
    TORCH_OK = False

_cuda_streams: List["torch.cuda.Stream"] = []
_stream_lock = threading.Lock()


def setup_gpu(
    use_gpu: bool = True,
    benchmark: bool = True,
    deterministic: bool = False,
    tf32: bool = True,
) -> str:
    """Setup GPU and return device string.

    Returns: "cuda" | "cpu"
    """
    if not TORCH_OK:
        logger.warning("PyTorch not available - using CPU")
        return "cpu"

    if not use_gpu or not torch.cuda.is_available():
        logger.info("GPU disabled or unavailable - using CPU")
        return "cpu"

    device_count  = torch.cuda.device_count()
    gpu_name      = torch.cuda.get_device_name(0)
    total_mem_gb  = torch.cuda.get_device_properties(0).total_memory / 1e9
    cuda_ver      = torch.version.cuda or "unknown"
    torch_ver     = torch.__version__

    logger.info(f"GPU detected: {gpu_name}")
    logger.info(f"   VRAM   : {total_mem_gb:.1f} GB")
    logger.info(f"   CUDA   : {cuda_ver}")
    logger.info(f"   PyTorch: {torch_ver}")
    logger.info(f"   Device : {device_count} GPU(s)")

    _check_rtx5000_support(gpu_name, cuda_ver, torch_ver)

    if deterministic:
        cudnn.deterministic = True
        cudnn.benchmark     = False
        torch.use_deterministic_algorithms(True, warn_only=True)
        logger.info("   cudnn : deterministic mode")
    elif benchmark:
        cudnn.benchmark     = True
        cudnn.deterministic = False
        logger.info("   cudnn : benchmark mode")

    if tf32 and _supports_tf32():
        torch.backends.cuda.matmul.allow_tf32 = True
        cudnn.allow_tf32                       = True
        logger.info("   TF32  : enabled (Blackwell Tensor Core)")

    os.environ.setdefault("CUDA_LAUNCH_BLOCKING", "0")

    _init_cuda_streams(n=3)
    _warmup_cuda_full(seq_len=60, features=120)

    logger.success(f"GPU setup complete: {gpu_name} ({total_mem_gb:.1f} GB)")
    return "cuda"


def _init_cuda_streams(n: int = 3):
    """Initialize n CUDA streams for BiLSTM/TFT/CNN-LSTM parallelism."""
    global _cuda_streams
    if not TORCH_OK or not torch.cuda.is_available():
        return
    with _stream_lock:
        _cuda_streams = [torch.cuda.Stream() for _ in range(n)]
    logger.info(f"   CUDA streams: {n} initialized (BiLSTM/TFT/CNN-LSTM)")


def get_cuda_stream(idx: int = 0) -> Optional["torch.cuda.Stream"]:
    """Return CUDA stream for given index."""
    if not _cuda_streams:
        return None
    return _cuda_streams[idx % len(_cuda_streams)]


def _warmup_cuda_full(seq_len: int = 60, features: int = 120):
    """Warmup CUDA with real input shape (1, seq_len, features)."""
    if not TORCH_OK or not torch.cuda.is_available():
        return
    try:
        logger.debug("   CUDA warmup starting (real input shape)...")
        device = "cuda"

        dummy = torch.randn(1, seq_len, features, device=device)

        for i, stream in enumerate(_cuda_streams):
            with torch.cuda.stream(stream):
                _ = dummy @ dummy.transpose(-2, -1)
                _ = dummy.mean(dim=1)

        torch.cuda.synchronize()

        with torch.amp.autocast(device_type="cuda", enabled=True):
            dummy_fp16 = torch.randn(1, seq_len, features, device=device)
            _ = dummy_fp16.mean()
        torch.cuda.synchronize()

        del dummy, dummy_fp16
        torch.cuda.empty_cache()
        logger.debug("   CUDA warmup complete (FP32 + FP16 x 3 streams)")
    except Exception as e:
        logger.debug(f"   CUDA warmup failed (non-critical): {e}")


def warmup_keep_alive(seq_len: int = 60, features: int = 120):
    """Keep CUDA context alive (called every 5 minutes)."""
    if not TORCH_OK or not torch.cuda.is_available():
        return
    try:
        dummy = torch.randn(1, seq_len, features, device="cuda")
        _ = dummy.sum()
        torch.cuda.synchronize()
        del dummy
        logger.debug("CUDA context keep-alive OK")
    except Exception as e:
        logger.debug(f"CUDA keep-alive failed: {e}")


def _check_rtx5000_support(gpu_name: str, cuda_ver: str, torch_ver: str):
    """Check RTX 50xx (Blackwell) compatibility."""
    rtx5k_keywords = ["5060", "5070", "5080", "5090", "5050"]
    if not any(k in gpu_name for k in rtx5k_keywords):
        return

    try:
        major, minor = (int(x) for x in (cuda_ver or "0.0").split(".")[:2])
        cuda_ok = (major > 12) or (major == 12 and minor >= 8)
    except Exception:
        cuda_ok = False

    try:
        tv       = torch_ver.split("+")[0]
        parts    = tv.split(".")
        major_t  = int(parts[0])
        minor_t  = int(parts[1]) if len(parts) > 1 else 0
        torch_ok = (major_t > 2) or (major_t == 2 and minor_t >= 6)
    except Exception:
        torch_ok = False

    if cuda_ok and torch_ok:
        logger.success(
            f"RTX 50xx (Blackwell) compatible: CUDA {cuda_ver} / PyTorch {torch_ver}"
        )
    else:
        logger.warning(
            "RTX 50xx detected but versions may be incompatible.\n"
            "   pip install --pre torch torchvision torchaudio "
            "--index-url https://download.pytorch.org/whl/nightly/cu128"
        )


def _supports_tf32() -> bool:
    """Check if GPU supports TF32 (Ampere+)."""
    if not TORCH_OK or not torch.cuda.is_available():
        return False
    props = torch.cuda.get_device_properties(0)
    return (props.major, props.minor) >= (8, 0)


def maybe_compile(model, **kwargs):
    """Skip torch.compile on Windows + RTX 5060 (use inference_mode + AMP instead)."""
    try:
        logger.info("torch.compile skipped on Windows - using inference_mode + AMP")
    except ImportError as _e:
        import logging as _lg
        _lg.getLogger("gpu_utils").debug(f"[WARN] gpu_utils 오류 무시: {_e}")
        pass
    return model


def get_gpu_memory_info() -> Dict:
    """Return GPU memory stats as dict."""
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
    """Clear GPU memory cache."""
    if TORCH_OK and torch.cuda.is_available():
        torch.cuda.empty_cache()
        logger.debug("GPU cache cleared")


def log_gpu_status():
    """Log current GPU memory status."""
    info = get_gpu_memory_info()
    if not info.get("available"):
        return
    logger.info(
        f"GPU | "
        f"used={info['allocated_gb']:.2f}GB / "
        f"reserved={info['reserved_gb']:.2f}GB / "
        f"total={info['total_gb']:.2f}GB "
        f"({info['utilization_pct']:.1f}%) | "
        f"streams={info['stream_count']}"
    )


def get_torch_install_cmd(gpu_name: str = "") -> str:
    """Return recommended pip install command for the detected GPU."""
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