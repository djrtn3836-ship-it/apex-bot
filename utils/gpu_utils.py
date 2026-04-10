"""
APEX BOT - GPU 理쒖쟻???좏떥由ы떚 v2.0
RTX 5060 (Blackwell) CUDA 理쒖쟻??
Step 2 理쒖쟻??
  - ?ㅼ젣 異붾줎 ?낅젰 ?뺥깭 (1,60,120)濡??뚮컢??(?덉씠?댁떆 ?꾩쟾 ?쒓굅)
  - 3媛?CUDA ?ㅽ듃由??앹꽦 ??BiLSTM/TFT/CNN-LSTM 蹂묐젹 異붾줎
  - 5遺꾨쭏??CUDA context ?좎? ?ㅼ?以꾨윭 ?곕룞
  - Blackwell SM 9.0 Tensor Core 理쒕? ?쒖슜
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

# ?? CUDA ?ㅽ듃由??꾩뿭 ?덉??ㅽ듃由???????????????????????????????????
_cuda_streams: List["torch.cuda.Stream"] = []
_stream_lock = threading.Lock()


def setup_gpu(
    use_gpu: bool = True,
    benchmark: bool = True,
    deterministic: bool = False,
    tf32: bool = True,
) -> str:
    """
        """Returns: cuda or cpu"""

    Returns: "cuda" | "cpu"
    """
    if not TORCH_OK:
        logger.warning("?좑툘  PyTorch 誘몄꽕移???CPU 紐⑤뱶")
        return "cpu"

    if not use_gpu or not torch.cuda.is_available():
        logger.info("?뮲 GPU 鍮꾪솢?깊솕 ??CPU 紐⑤뱶")
        return "cpu"

    device_count  = torch.cuda.device_count()
    gpu_name      = torch.cuda.get_device_name(0)
    total_mem_gb  = torch.cuda.get_device_properties(0).total_memory / 1e9
    cuda_ver      = torch.version.cuda or "unknown"
    torch_ver     = torch.__version__

    logger.info(f"?? GPU 媛먯?: {gpu_name}")
    logger.info(f"   VRAM   : {total_mem_gb:.1f} GB")
    logger.info(f"   CUDA   : {cuda_ver}")
    logger.info(f"   PyTorch: {torch_ver}")
    logger.info(f"   Device : {device_count}媛?)

    _check_rtx5000_support(gpu_name, cuda_ver, torch_ver)

    # ?? cudnn ?ㅼ젙 ?????????????????????????????????????????????
    if deterministic:
        cudnn.deterministic = True
        cudnn.benchmark     = False
        torch.use_deterministic_algorithms(True, warn_only=True)
        logger.info("   cudnn : deterministic mode")
    elif benchmark:
        cudnn.benchmark     = True
        cudnn.deterministic = False
        logger.info("   cudnn : benchmark mode (?띾룄 理쒖쟻??")

    # ?? TF32 (Ampere+ / Blackwell Tensor Core) ?????????????????
    if tf32 and _supports_tf32():
        torch.backends.cuda.matmul.allow_tf32 = True
        cudnn.allow_tf32                       = True
        logger.info("   TF32  : ?쒖꽦??(Blackwell Tensor Core 媛??")

    # ?? 鍮꾨룞湲??ㅽ뻾 ?좎? ????????????????????????????????????????
    os.environ.setdefault("CUDA_LAUNCH_BLOCKING", "0")

    # ?? 3媛?CUDA ?ㅽ듃由?珥덇린??(BiLSTM / TFT / CNN-LSTM) ????????
    _init_cuda_streams(n=3)

    # ?? ?ㅼ젣 異붾줎 ?낅젰 ?뺥깭濡??뚮컢?????????????????????????????
    _warmup_cuda_full(seq_len=60, features=120)

    logger.success(f"??GPU 珥덇린???꾨즺: {gpu_name} ({total_mem_gb:.1f} GB)")
    return "cuda"


def _init_cuda_streams(n: int = 3):
    """
    ??Step 2 ?좉퇋: CUDA ?ㅽ듃由?n媛?珥덇린??    RTX 5060 Blackwell ??硫?곗뒪?몃┝ SM 蹂묐젹 ?쒖슜
    """
    global _cuda_streams
    if not TORCH_OK or not torch.cuda.is_available():
        return
    with _stream_lock:
        _cuda_streams = [torch.cuda.Stream() for _ in range(n)]
    logger.info(f"   CUDA ?ㅽ듃由?{n}媛?珥덇린??(BiLSTM/TFT/CNN-LSTM 蹂묐젹)")


def get_cuda_stream(idx: int = 0) -> Optional["torch.cuda.Stream"]:
    """紐⑤뜽 ?몃뜳?ㅼ뿉 留욌뒗 CUDA ?ㅽ듃由?諛섑솚"""
    if not _cuda_streams:
        return None
    return _cuda_streams[idx % len(_cuda_streams)]


def _warmup_cuda_full(seq_len: int = 60, features: int = 120):
    """
    ??Step 2 媛쒖꽑: ?ㅼ젣 異붾줎 ?낅젰 (1, seq_len, features) ?뺥깭濡??뚮컢??    湲곗〈 512횞512 ?됰젹 ???ㅼ젣 紐⑤뜽 ?낅젰 ?ш린濡?蹂寃?    泥?異붾줎 ?덉씠?댁떆 ?꾩쟾 ?쒓굅
    """
    if not TORCH_OK or not torch.cuda.is_available():
        return
    try:
        logger.debug("   CUDA ?뚮컢???쒖옉 (?ㅼ젣 異붾줎 ?낅젰 ?뺥깭)...")
        device = "cuda"

        # ?ㅼ젣 ?숈긽釉?紐⑤뜽 ?낅젰 ?ш린
        dummy = torch.randn(1, seq_len, features, device=device)

        # 3媛??ㅽ듃由쇱뿉???숈떆 ?뚮컢??        for i, stream in enumerate(_cuda_streams):
            with torch.cuda.stream(stream):
                _ = dummy @ dummy.transpose(-2, -1)
                _ = dummy.mean(dim=1)

        # 紐⑤뱺 ?ㅽ듃由??숆린??        torch.cuda.synchronize()

        # 異붽?: FP16 (AMP) ?뚮컢??        with torch.amp.autocast(device_type="cuda", enabled=True):
            dummy_fp16 = torch.randn(1, seq_len, features, device=device)
            _ = dummy_fp16.mean()
        torch.cuda.synchronize()

        del dummy, dummy_fp16
        torch.cuda.empty_cache()
        logger.debug("   CUDA ?뚮컢???꾨즺 (FP32 + FP16 횞 3?ㅽ듃由?")
    except Exception as e:
        logger.debug(f"   CUDA ?뚮컢???ㅽ뙣 (臾댁떆): {e}")


def warmup_keep_alive(seq_len: int = 60, features: int = 120):
    """
    ??Step 2 ?좉퇋: CUDA context ?좎? (5遺꾨쭏???ㅼ?以꾨윭?먯꽌 ?몄텧)
    GPU媛 ?덉쟾 紐⑤뱶濡??꾪솚?섎뒗 寃껋쓣 諛⑹?
    """
    if not TORCH_OK or not torch.cuda.is_available():
        return
    try:
        dummy = torch.randn(1, seq_len, features, device="cuda")
        _ = dummy.sum()
        torch.cuda.synchronize()
        del dummy
        logger.debug("??CUDA context ?좎? (keep-alive)")
    except Exception as e:
        logger.debug(f"CUDA keep-alive ?ㅽ뙣: {e}")


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
            f"??RTX 50xx (Blackwell) ?꾩쟾 吏?? CUDA {cuda_ver} / PyTorch {torch_ver}"
        )
    else:
        logger.warning(
            "?좑툘  RTX 50xx 媛먯? ??理쒖쟻 ?깅뒫???꾪빐:\n"
            "   pip install --pre torch torchvision torchaudio "
            "--index-url https://download.pytorch.org/whl/nightly/cu128"
        )


def _supports_tf32() -> bool:
    if not TORCH_OK or not torch.cuda.is_available():
        return False
    props = torch.cuda.get_device_properties(0)
    return (props.major, props.minor) >= (8, 0)


# ?? torch.compile ?섑띁 ?????????????????????????????????????????
def maybe_compile(model, **kwargs):
    """
    Windows + RTX 5060 ?섍꼍?먯꽌 torch.compile ???    inference_mode + AMP留??ъ슜 (FX tracing 異⑸룎 諛⑹?)
    """
    try:
        import torch
        logger.info("??torch.compile 鍮꾪솢?깊솕 (Windows ?덉쟾紐⑤뱶) ??inference_mode+AMP ?ъ슜")
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
        logger.debug("?뿊截? GPU 罹먯떆 ?뺣━")


def log_gpu_status():
    info = get_gpu_memory_info()
    if not info.get("available"):
        return
    logger.info(
        f"?뮶 GPU | "
        f"?ъ슜={info['allocated_gb']:.2f}GB / "
        f"?덉빟={info['reserved_gb']:.2f}GB / "
        f"?꾩껜={info['total_gb']:.2f}GB "
        f"({info['utilization_pct']:.1f}%) | "
        f"?ㅽ듃由?{info['stream_count']}媛?
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
