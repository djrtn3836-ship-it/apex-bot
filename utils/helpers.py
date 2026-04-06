"""
APEX BOT - 유틸리티 헬퍼 함수
공통 유틸리티: 시간, 포맷팅, 수학, 재시도 로직
"""
import asyncio
import time
import functools
from datetime import datetime, timezone
from typing import Callable, Any, Optional
from loguru import logger


# ── 시간 유틸리티 ──────────────────────────────────────────────────
def now_kst() -> datetime:
    """현재 한국 시간 반환"""
    from datetime import timedelta
    utc_now = datetime.now(timezone.utc)
    kst = timezone(timedelta(hours=9))
    return utc_now.astimezone(kst)


def ts_to_datetime(ts_ms: int) -> datetime:
    """밀리초 타임스탬프 → datetime"""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


def format_currency(amount: float, currency: str = "KRW") -> str:
    """금액 포맷팅"""
    if currency == "KRW":
        return f"₩{amount:,.0f}"
    return f"{amount:.8f} {currency}"


def format_percent(value: float) -> str:
    """퍼센트 포맷팅"""
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


# ── 수학 유틸리티 ──────────────────────────────────────────────────
def safe_divide(a: float, b: float, default: float = 0.0) -> float:
    """안전한 나눗셈"""
    return a / b if b != 0 else default


def clamp(value: float, min_val: float, max_val: float) -> float:
    """값 범위 제한"""
    return max(min_val, min(max_val, value))


def round_price(price: float, market: str = "KRW-BTC") -> float:
    """업비트 호가 단위에 맞게 반올림"""
    if price >= 2_000_000:
        return round(price / 1000) * 1000
    elif price >= 1_000_000:
        return round(price / 500) * 500
    elif price >= 500_000:
        return round(price / 100) * 100
    elif price >= 100_000:
        return round(price / 50) * 50
    elif price >= 10_000:
        return round(price / 10) * 10
    elif price >= 1_000:
        return round(price / 1) * 1
    elif price >= 100:
        return round(price * 10) / 10
    elif price >= 10:
        return round(price * 100) / 100
    else:
        return round(price * 1000) / 1000


def calculate_profit_rate(entry: float, current: float, fee_rate: float = 0.001) -> float:
    """수익률 계산 (수수료 반영)"""
    gross = (current - entry) / entry
    return gross - (fee_rate * 2)  # 매수 + 매도 수수료


# ── 재시도 데코레이터 ──────────────────────────────────────────────
def retry(max_attempts: int = 3, delay: float = 1.0, exceptions=(Exception,)):
    """동기 함수 재시도 데코레이터"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts - 1:
                        logger.error(f"❌ {func.__name__} 최대 재시도 초과: {e}")
                        raise
                    wait = delay * (2 ** attempt)
                    logger.warning(f"⚠️ {func.__name__} 재시도 {attempt+1}/{max_attempts} ({wait}s 후)")
                    time.sleep(wait)
        return wrapper
    return decorator


def async_retry(max_attempts: int = 3, delay: float = 1.0, exceptions=(Exception,)):
    """비동기 함수 재시도 데코레이터"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts - 1:
                        logger.error(f"❌ {func.__name__} 최대 재시도 초과: {e}")
                        raise
                    wait = delay * (2 ** attempt)
                    logger.warning(f"⚠️ {func.__name__} 재시도 {attempt+1}/{max_attempts} ({wait}s 후)")
                    await asyncio.sleep(wait)
        return wrapper
    return decorator


# ── 성능 측정 ──────────────────────────────────────────────────────
class Timer:
    """코드 블록 실행 시간 측정"""
    def __init__(self, name: str = ""):
        self.name = name
        self._start = None

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args):
        elapsed = (time.perf_counter() - self._start) * 1000
        if self.name:
            logger.debug(f"⏱️ {self.name}: {elapsed:.2f}ms")
        self.elapsed_ms = elapsed


# ── 레이트 리미터 ──────────────────────────────────────────────────
class RateLimiter:
    """API 호출 레이트 제한"""
    def __init__(self, calls_per_second: float = 10):
        self.calls_per_second = calls_per_second
        self._calls = []
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            # 1초 이내 호출만 유지
            self._calls = [t for t in self._calls if now - t < 1.0]
            if len(self._calls) >= self.calls_per_second:
                sleep_time = 1.0 - (now - self._calls[0])
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
            self._calls.append(time.monotonic())


# ── 시장 유틸리티 ──────────────────────────────────────────────────
def extract_coin(market: str) -> str:
    """'KRW-BTC' → 'BTC'"""
    return market.split("-")[1] if "-" in market else market


def is_market_open() -> bool:
    """업비트는 24/7 운영 (항상 True)"""
    return True


def timeframe_to_minutes(tf: str) -> int:
    """타임프레임 문자열 → 분 변환"""
    mapping = {"1": 1, "5": 5, "15": 15, "60": 60, "240": 240, "1440": 1440}
    return mapping.get(tf, 60)
