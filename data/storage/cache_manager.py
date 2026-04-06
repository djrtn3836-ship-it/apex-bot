"""
APEX BOT - 인메모리 캐시 관리자 v2.0
DDR5-5600 32GB 최적화

Step 2 최적화:
  - 코인별 OHLCV 전량 RAM 상주 (10코인 × 2000봉 ≈ 800MB)
  - NpyCache 연동: 부팅 시 NVMe → RAM 워밍업
  - get_*() 호출 시 NVMe fallback 자동
  - 메모리 사용량 실시간 모니터링
"""
import time
from typing import Any, Dict, Optional, List, Tuple
from collections import defaultdict, deque
import numpy as np
from loguru import logger

from config.settings import get_settings


class CacheManager:
    """
    고속 인메모리 캐시 관리자
    DDR5-5600 32GB 전략적 활용

    캐시 계층:
      L1: Python dict (ns 접근)   ← 현재가, 최신 신호
      L2: numpy array (μs 접근)  ← OHLCV + 지표 전량
      L3: NvMe mmap (ms 접근)    ← 장기 보관 캔들
    """

    def __init__(self):
        self.settings     = get_settings()
        max_candles       = self.settings.database.cache_max_candles
        max_ticks         = self.settings.database.cache_max_ticks

        # ── L1: 현재가 / 신호 캐시 ───────────────────────────
        self.price_cache:     Dict[str, Tuple[float, float]] = {}  # (price, ts)
        self.signal_cache:    Dict[str, dict]                = {}
        self.orderbook_cache: Dict[str, dict]                = {}

        # ── L2: OHLCV + 지표 캔들 캐시 (DDR5 상주) ───────────
        self.candle_cache: Dict[str, Dict[str, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=max_candles))
        )

        # ── L2: 실시간 틱 캐시 ───────────────────────────────
        self.tick_cache: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=max_ticks)
        )

        # ── TTL 일반 캐시 ─────────────────────────────────────
        self._ttl_cache: Dict[str, Dict] = {}

        # ── NpyCache 연동 (L3) ────────────────────────────────
        try:
            from data.storage.npy_cache import get_npy_cache
            self._npy_cache = get_npy_cache()
            logger.info("✅ NpyCache 연동 완료 (NVMe L3 캐시)")
        except Exception as e:
            self._npy_cache = None
            logger.debug(f"NpyCache 연동 실패 (무시): {e}")

        logger.info(
            f"✅ CacheManager 초기화 | "
            f"캔들={max_candles}/코인 | 틱={max_ticks}/코인 | "
            f"추정 메모리≈{self._estimate_memory_mb():.0f}MB"
        )

    # ── 가격 캐시 ─────────────────────────────────────────────────

    def set_price(self, market: str, price: float):
        self.price_cache[market] = (price, time.time())

    def get_price(self, market: str, max_age: float = 5.0) -> Optional[float]:
        if market not in self.price_cache:
            return None
        price, ts = self.price_cache[market]
        if time.time() - ts > max_age:
            return None
        return price

    def get_all_prices(self) -> Dict[str, float]:
        now = time.time()
        return {
            m: p for m, (p, ts) in self.price_cache.items()
            if now - ts <= 10.0
        }

    # ── 틱 캐시 ──────────────────────────────────────────────────

    def add_tick(self, market: str, tick: dict):
        self.tick_cache[market].append({
            "price":    tick.get("trade_price", 0),
            "volume":   tick.get("trade_volume", 0),
            "timestamp": tick.get("timestamp", time.time()),
            "ask_bid":  tick.get("ask_bid", ""),
        })

    def get_recent_ticks(self, market: str, n: int = 100) -> List[dict]:
        ticks = list(self.tick_cache[market])
        return ticks[-n:] if len(ticks) >= n else ticks

    def get_tick_vwap(self, market: str, n: int = 200) -> float:
        ticks = self.get_recent_ticks(market, n)
        if not ticks:
            return 0.0
        prices  = np.array([t["price"]  for t in ticks])
        volumes = np.array([t["volume"] for t in ticks])
        total   = volumes.sum()
        return float((prices * volumes).sum() / total) if total > 0 else float(prices.mean())

    def get_buy_sell_ratio(self, market: str, n: int = 100) -> float:
        ticks    = self.get_recent_ticks(market, n)
        if not ticks:
            return 1.0
        buy_vol  = sum(t["volume"] for t in ticks if t["ask_bid"] == "BID")
        sell_vol = sum(t["volume"] for t in ticks if t["ask_bid"] == "ASK")
        return buy_vol / (sell_vol + 1e-10)

    # ── 호가창 캐시 ───────────────────────────────────────────────

    def set_orderbook(self, market: str, orderbook: dict):
        self.orderbook_cache[market] = orderbook

    def get_orderbook(self, market: str) -> Optional[dict]:
        return self.orderbook_cache.get(market)

    def get_bid_ask_spread(self, market: str) -> Optional[float]:
        ob    = self.orderbook_cache.get(market)
        if not ob:
            return None
        units = ob.get("orderbook_units", [])
        if not units:
            return None
        best_ask = units[0].get("ask_price", 0)
        best_bid = units[0].get("bid_price", 0)
        if best_ask > 0:
            return (best_ask - best_bid) / best_ask * 100
        return None

    # ── 신호 캐시 ─────────────────────────────────────────────────

    def set_signal(self, market: str, signal: dict):
        self.signal_cache[market] = {**signal, "timestamp": time.time()}

    def get_signal(self, market: str, max_age: float = 300) -> Optional[dict]:
        sig = self.signal_cache.get(market)
        if sig and time.time() - sig.get("timestamp", 0) <= max_age:
            return sig
        return None

    # ── TTL 일반 캐시 ─────────────────────────────────────────────

    def set(self, key: str, value: Any, ttl: float = 60.0):
        self._ttl_cache[key] = {"value": value, "expires": time.time() + ttl}

    def get(self, key: str) -> Optional[Any]:
        item = self._ttl_cache.get(key)
        if item and time.time() < item["expires"]:
            return item["value"]
        self._ttl_cache.pop(key, None)
        return None

    # ── NpyCache 연동 (L3 → L2 워밍업) ──────────────────────────

    def warmup_from_npy(self, markets: List[str], timeframes: List[str] = None):
        """
        ✅ Step 2: 부팅 시 NVMe 캐시 → RAM 워밍업
        Crucial E100 NVMe → DDR5 RAM 전송
        """
        if self._npy_cache is None:
            return
        if timeframes is None:
            timeframes = ["60", "1440"]

        loaded = 0
        for market in markets:
            for tf in timeframes:
                if self._npy_cache.is_fresh(market, tf, max_age_seconds=3600):
                    df = self._npy_cache.load(market, tf)
                    if df is not None and not df.empty:
                        for _, row in df.iterrows():
                            self.candle_cache[market][tf].append(row.to_dict())
                        loaded += 1
                        logger.debug(
                            f"⚡ NVMe→RAM 워밍업: {market}/{tf} | {len(df)}행"
                        )

        if loaded > 0:
            logger.info(
                f"✅ NVMe 캐시 워밍업 완료: {loaded}개 마켓/타임프레임 로드"
            )

    def save_to_npy(self, market: str, timeframe: str, df):
        """pandas DataFrame을 NpyCache에 비동기 저장"""
        if self._npy_cache is None or df is None:
            return
        try:
            self._npy_cache.save(market, timeframe, df)
        except Exception as e:
            logger.debug(f"NpyCache 저장 실패 ({market}/{timeframe}): {e}")

    # ── 메모리 모니터링 ───────────────────────────────────────────

    def _estimate_memory_mb(self) -> float:
        """캐시 예상 메모리 사용량 (MB) 추정"""
        max_c       = self.settings.database.cache_max_candles
        markets     = len(self.settings.trading.target_markets)
        # 캔들 1개 ≈ 200바이트 (지표 포함)
        candle_mb   = markets * max_c * 200 / 1e6
        # 틱 1개 ≈ 64바이트
        tick_mb     = markets * self.settings.database.cache_max_ticks * 64 / 1e6
        return candle_mb + tick_mb

    def get_memory_usage_mb(self) -> float:
        """실제 메모리 사용량 (MB)"""
        try:
            import psutil, os
            proc = psutil.Process(os.getpid())
            return proc.memory_info().rss / 1e6
        except Exception:
            return 0.0




    # ── OHLCV 래퍼 (NpyCache 위임) ─────────────────────────────────
    def get_ohlcv(self, market: str, interval: str = "1h"):
        """NpyCache에서 OHLCV DataFrame 반환. 없으면 None."""
        try:
            npy = getattr(self, '_npy_cache', None)
            if npy is not None:
                # load() 또는 get() 메서드 자동 감지
                if hasattr(npy, 'load'):
                    df = npy.load(market, interval)
                elif hasattr(npy, 'get'):
                    df = npy.get(market, interval)
                else:
                    df = None
                if df is not None and len(df) > 0:
                    return df
        except Exception:
            pass
        return None

    def get_candles(self, market: str, interval: str = "1h"):
        """get_ohlcv 별칭 (하위 호환)."""
        return self.get_ohlcv(market, interval)

    def set_ohlcv(self, market: str, interval: str, df) -> None:
        """NpyCache에 OHLCV DataFrame 저장."""
        try:
            npy = getattr(self, '_npy_cache', None)
            if npy is not None:
                if hasattr(npy, 'save'):
                    npy.save(market, interval, df)
                elif hasattr(npy, 'set'):
                    npy.set(market, interval, df)
        except Exception:
            pass

    def get_stats(self) -> dict:
        total_ticks = sum(len(v) for v in self.tick_cache.values())
        npy_size_mb = self._npy_cache.get_cache_size_mb() if self._npy_cache else 0
        return {
            "markets_tracked": len(self.price_cache),
            "total_ticks":     total_ticks,
            "orderbooks":      len(self.orderbook_cache),
            "signals":         len(self.signal_cache),
            "npy_cache_mb":    round(npy_size_mb, 1),
            "ram_usage_mb":    round(self.get_memory_usage_mb(), 1),
            "estimated_mb":    round(self._estimate_memory_mb(), 1),
        }

    def clear_market(self, market: str):
        self.tick_cache.pop(market, None)
        self.orderbook_cache.pop(market, None)
        self.price_cache.pop(market, None)
        self.signal_cache.pop(market, None)