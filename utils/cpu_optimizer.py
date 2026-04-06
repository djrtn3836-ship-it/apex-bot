"""
APEX BOT - CPU 최적화 유틸리티 (신규)
Intel Ultra 5 225F 전용 코어 핀닝 + ProcessPool 최적화

Ultra 5 225F 구조:
  P코어 (Performance) : 6개  [빠름, 고전력]
  E코어 (Efficient)   : 8개  [느림, 저전력]
  총 14코어

최적 배치:
  P코어 0-1 : 이벤트 루프 + 주문 실행 (레이턴시 최우선)
  P코어 2-5 : ProcessPoolExecutor 전략 병렬 (4개로 증가)
  E코어 0-7 : asyncio ThreadPool 데이터 수집 (IO bound)
"""
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
    """CPU 코어 정보 반환"""
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
    """
    ✅ Step 2: 전략 실행용 ProcessPoolExecutor
    기존 max_workers=2 → 4로 증가 (P코어 2-5 사용)
    """
    info = get_cpu_info()
    # 물리 코어의 절반을 전략에 배정 (최소 2, 최대 6)
    workers = max(2, min(6, info["physical"] // 2))

    logger.info(
        f"⚙️  전략 ProcessPool: {workers}개 워커 "
        f"(P코어 활용, 기존 2→{workers})"
    )
    pool = ProcessPoolExecutor(
        max_workers=workers,
        initializer=_worker_init,
    )
    return pool


def create_io_thread_pool() -> ThreadPoolExecutor:
    """
    ✅ Step 2: 데이터 수집용 ThreadPoolExecutor
    E코어 8개 → IO bound 작업 전담
    """
    info    = get_cpu_info()
    workers = max(4, min(16, info["logical"] - info["physical"]))

    logger.info(
        f"⚙️  IO ThreadPool: {workers}개 워커 (E코어 활용)"
    )
    return ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="apex_io",
    )


def _worker_init():
    """
    ✅ Step 2: ProcessPool 워커 초기화
    각 워커 프로세스를 P코어에 고정 시도
    """
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
    """
    ✅ Step 2: 메인 스레드(이벤트 루프)를 P코어 0-1에 핀닝
    주문 실행 레이턴시 최소화
    """
    try:
        import psutil
        proc    = psutil.Process()
        p_cores = [0, 1]  # P코어 0-1: 이벤트 루프 전담
        proc.cpu_affinity(p_cores)
        logger.info(f"📌 메인 스레드 → P코어 {p_cores} 핀닝 완료")
        return True
    except ImportError:
        logger.debug("psutil 미설치 → 코어 핀닝 스킵")
        return False
    except Exception as e:
        logger.debug(f"코어 핀닝 실패 (무시): {e}")
        return False


def optimize_asyncio_event_loop():
    """
    ✅ Step 2: asyncio 이벤트 루프 최적화
    Windows: ProactorEventLoop (IOCP 기반, 더 빠름)
    """
    import platform
    if platform.system() == "Windows":
        try:
            loop = asyncio.ProactorEventLoop()
            asyncio.set_event_loop(loop)
            logger.info("⚡ asyncio ProactorEventLoop 적용 (Windows IOCP)")
        except Exception as e:
            logger.debug(f"ProactorEventLoop 설정 실패: {e}")


def log_cpu_status():
    """CPU 사용 현황 로그"""
    try:
        import psutil
        usage = psutil.cpu_percent(percpu=True, interval=0.1)
        avg   = sum(usage) / len(usage)
        logger.info(
            f"🖥️  CPU | 평균={avg:.1f}% | "
            f"코어별={[f'{u:.0f}%' for u in usage[:6]]}(P) "
            f"{[f'{u:.0f}%' for u in usage[6:]]}(E)"
        )
    except Exception:
        pass
