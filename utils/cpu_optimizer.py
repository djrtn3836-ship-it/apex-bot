"""APEX BOT - CPU   ()
Intel Ultra 5 225F    + ProcessPool 

Ultra 5 225F :
  P (Performance) : 6  [, ]
  E (Efficient)   : 8  [, ]
   14

 :
  P 0-1 :   +   ( )
  P 2-5 : ProcessPoolExecutor   (4 )
  E 0-7 : asyncio ThreadPool   (IO bound)"""
from __future__ import annotations

import os
import sys
import asyncio
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import Optional, Tuple
from loguru import logger


# ── 코어 레이아웃 (Ultra 5 225F) ────────────────────────────────
# 실제 코어 번호는 OS별로 다를 수 있으므로 psutil로 자동 감지
_P_CORE_COUNT = 6
_E_CORE_COUNT = 8
_TOTAL_CORES  = 14

# ProcessPoolExecutor 워커 수 (P코어 4개 사용)
STRATEGY_WORKERS = 4   # 기존 2개 → 4개 (2배 향상)

# ThreadPoolExecutor 워커 수 (E코어 8개 사용)
IO_WORKERS = 8


def get_cpu_info() -> dict:
    """CPU"""
    try:
        import psutil
        logical  = psutil.cpu_count(logical=True)
        physical = psutil.cpu_count(logical=False)
        return {
            "logical":  logical,
            "physical": physical,
            "is_ultra5_225f": (physical == 14 or logical == 14),
        }
    except ImportError:
        return {"logical": os.cpu_count() or 4, "physical": 7, "is_ultra5_225f": False}


def create_strategy_pool() -> ProcessPoolExecutor:
    """Step 2:   ProcessPoolExecutor
     max_workers=2 → 4  (P 2-5 )"""
    info = get_cpu_info()
    # 물리 코어의 절반을 전략에 배정 (최소 2, 최대 6)
    workers = max(2, min(6, info["physical"] // 2))

    logger.info(
        f"   ProcessPool: {workers}  "
        f"(P ,  2→{workers})"
    )
    pool = ProcessPoolExecutor(
        max_workers=workers,
        initializer=_worker_init,
    )
    return pool


def create_io_thread_pool() -> ThreadPoolExecutor:
    """Step 2:   ThreadPoolExecutor
    E 8 → IO bound"""
    info    = get_cpu_info()
    workers = max(4, min(16, info["logical"] - info["physical"]))

    logger.info(
        f"  IO ThreadPool: {workers}  (E )"
    )
    return ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="apex_io",
    )


def _worker_init():
    """Step 2: ProcessPool  
       P"""
    try:
        import psutil
        proc  = psutil.Process()
        pid   = os.getpid()
        # P코어 범위 (논리 코어 0-11 중 0-5를 P코어로 가정)
        p_cores = list(range(min(6, psutil.cpu_count(logical=True))))
        if p_cores:
            proc.cpu_affinity(p_cores)
    except Exception:
        pass  # psutil 없거나 권한 없으면 무시


def pin_main_thread_to_pcores():
    """Step 2:  ( ) P 0-1"""
    try:
        import psutil
        proc    = psutil.Process()
        p_cores = [0, 1]  # P코어 0-1: 이벤트 루프 전담
        proc.cpu_affinity(p_cores)
        logger.info(f"   → P {p_cores}  ")
        return True
    except ImportError:
        logger.debug("psutil  →   ")
        return False
    except Exception as e:
        logger.debug(f"   (): {e}")
        return False


def optimize_asyncio_event_loop():
    """Step 2: asyncio   
    Windows: ProactorEventLoop (IOCP ,  )"""
    import platform
    if platform.system() == "Windows":
        try:
            loop = asyncio.ProactorEventLoop()
            asyncio.set_event_loop(loop)
            logger.info(" asyncio ProactorEventLoop  (Windows IOCP)")
        except Exception as e:
            logger.debug(f"ProactorEventLoop  : {e}")


def log_cpu_status():
    """CPU"""
    try:
        import psutil
        usage = psutil.cpu_percent(percpu=True, interval=0.1)
        avg   = sum(usage) / len(usage)
        logger.info(
            f"  CPU | ={avg:.1f}% | "
            f"={[f'{u:.0f}%' for u in usage[:6]]}(P) "
            f"{[f'{u:.0f}%' for u in usage[6:]]}(E)"
        )
    except Exception as _e:
        import logging as _lg
        _lg.getLogger("cpu_optimizer").debug(f"[WARN] cpu_optimizer 오류 무시: {_e}")
        pass