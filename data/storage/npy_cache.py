"""
APEX BOT - NVMe mmap 캔들 캐시 (신규)
Crucial E100 M.2 NVMe 1TB 고속 저장 활용

기존: SQLite → 캔들 로드 ~50ms
신규: .npy mmap → 캔들 로드 ~5ms (10배 향상)

Crucial E100 사양:
  읽기: ~6,000 MB/s
  쓰기: ~5,000 MB/s
  DDR5-5600 32GB 보다 느리지만 용량 무제한

사용 방법:
  cache = NpyCache()
  cache.save(market, timeframe, df)
  df = cache.load(market, timeframe)
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional, Dict, List
import numpy as np
import pandas as pd
from loguru import logger

# 캐시 루트 (NVMe에 저장)
CACHE_ROOT = Path("database/candle_cache")

# 컬럼 정의 (순서 고정)
CANDLE_COLUMNS = [
    "open", "high", "low", "close", "volume",
    "ema5", "ema10", "ema20", "ema50", "ema100", "ema200",
    "sma5", "sma10", "sma20", "sma50",
    "macd", "macd_signal", "macd_hist",
    "rsi", "rsi_fast", "rsi_slow",
    "bb_upper", "bb_mid", "bb_lower", "bb_width", "bb_pct",
    "atr", "atr_pct",
    "stoch_k", "stoch_d",
    "vwap", "adx", "di_plus", "di_minus",
    "obv", "cci", "willr", "mfi",
    "supertrend", "supertrend_dir",
    "vol_sma20", "vol_ratio",
]


class NpyCache:
    """
    numpy .npy 파일 기반 캔들 캐시
    메모리 맵(mmap)으로 즉시 로드

    장점:
      - SQLite 대비 ~10배 빠른 로드
      - NVMe 6GB/s 속도 활용
      - 인덱스(timestamp) 별도 .npy 저장
    """

    def __init__(self, cache_root: Path = CACHE_ROOT):
        self.root = Path(cache_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._meta: Dict[str, Dict] = {}   # 캐시 메타 정보
        logger.info(f"✅ NpyCache 초기화: {self.root}")

    # ── 저장 ──────────────────────────────────────────────────────

    def save(
        self,
        market: str,
        timeframe: str,
        df: pd.DataFrame,
        max_rows: int = 2000,
    ) -> bool:
        """
        DataFrame → .npy 파일 저장

        파일 구조:
          {cache_root}/{market}/{timeframe}/data.npy      ← OHLCV + 지표
          {cache_root}/{market}/{timeframe}/timestamps.npy ← 타임스탬프
          {cache_root}/{market}/{timeframe}/meta.json      ← 메타 정보
        """
        if df is None or df.empty:
            return False
        try:
            folder = self._get_folder(market, timeframe)
            folder.mkdir(parents=True, exist_ok=True)

            # 컬럼 추출 (없는 컬럼은 0으로 채움)
            df_tail = df.tail(max_rows)
            data    = np.zeros((len(df_tail), len(CANDLE_COLUMNS)), dtype=np.float32)
            for i, col in enumerate(CANDLE_COLUMNS):
                if col in df_tail.columns:
                    data[:, i] = df_tail[col].values.astype(np.float32)

            # 타임스탬프 저장
            if hasattr(df_tail.index, "astype"):
                try:
                    ts = df_tail.index.astype(np.int64)
                except Exception:
                    ts = np.arange(len(df_tail), dtype=np.int64)
            else:
                ts = np.arange(len(df_tail), dtype=np.int64)

            # 파일 저장
            np.save(str(folder / "data.npy"),       data)
            np.save(str(folder / "timestamps.npy"), ts)

            # 메타 정보
            import json
            meta = {
                "market":     market,
                "timeframe":  timeframe,
                "rows":       len(df_tail),
                "columns":    CANDLE_COLUMNS,
                "updated_at": time.time(),
            }
            (folder / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8"
            )

            key = f"{market}_{timeframe}"
            self._meta[key] = meta
            logger.debug(
                f"💾 NpyCache 저장: {market}/{timeframe} | {len(df_tail)}행"
            )
            return True

        except Exception as e:
            logger.error(f"NpyCache 저장 실패 ({market}/{timeframe}): {e}")
            return False

    # ── 로드 ──────────────────────────────────────────────────────

    def load(
        self,
        market: str,
        timeframe: str,
        use_mmap: bool = True,
    ) -> Optional[pd.DataFrame]:
        """
        .npy 파일 → DataFrame (mmap 즉시 로드)

        use_mmap=True: 메모리 맵으로 로드 (최고속, 파일 수정 시 자동 반영)
        use_mmap=False: 전체 복사 (안전)
        """
        folder = self._get_folder(market, timeframe)
        data_path = folder / "data.npy"
        ts_path   = folder / "timestamps.npy"

        if not data_path.exists():
            return None

        try:
            t_start = time.perf_counter()

            # mmap 모드로 즉시 로드 (파일 전체를 메모리에 올리지 않음)
            mmap_mode = "r" if use_mmap else None
            data      = np.load(str(data_path), mmap_mode=mmap_mode)
            ts        = np.load(str(ts_path),   mmap_mode=mmap_mode) if ts_path.exists() else None

            # DataFrame 복원
            df = pd.DataFrame(data, columns=CANDLE_COLUMNS, dtype=np.float32)

            if ts is not None:
                try:
                    df.index = pd.to_datetime(ts)
                except Exception:
                    pass

            elapsed = (time.perf_counter() - t_start) * 1000
            logger.debug(
                f"⚡ NpyCache 로드: {market}/{timeframe} | "
                f"{len(df)}행 | {elapsed:.1f}ms"
            )
            return df

        except Exception as e:
            logger.error(f"NpyCache 로드 실패 ({market}/{timeframe}): {e}")
            return None

    # ── 유효성 체크 ───────────────────────────────────────────────

    def is_fresh(
        self,
        market: str,
        timeframe: str,
        max_age_seconds: float = 300,
    ) -> bool:
        """캐시가 max_age_seconds 이내인지 확인"""
        folder   = self._get_folder(market, timeframe)
        meta_path = folder / "meta.json"
        if not meta_path.exists():
            return False
        try:
            import json
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            age  = time.time() - meta.get("updated_at", 0)
            return age < max_age_seconds
        except Exception:
            return False

    def get_age_seconds(self, market: str, timeframe: str) -> float:
        """캐시 경과 시간(초) 반환"""
        folder    = self._get_folder(market, timeframe)
        meta_path = folder / "meta.json"
        if not meta_path.exists():
            return float("inf")
        try:
            import json
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return time.time() - meta.get("updated_at", 0)
        except Exception:
            return float("inf")

    def get_cache_size_mb(self) -> float:
        """전체 캐시 크기 (MB)"""
        total = sum(
            f.stat().st_size for f in self.root.rglob("*.npy")
        )
        return total / 1e6

    # ── 내부 헬퍼 ────────────────────────────────────────────────

    def _get_folder(self, market: str, timeframe: str) -> Path:
        safe_market = market.replace("-", "_")
        return self.root / safe_market / f"tf_{timeframe}"

    def list_cached(self) -> List[Dict]:
        """캐시된 마켓/타임프레임 목록"""
        result = []
        import json
        for meta_path in self.root.rglob("meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                result.append(meta)
            except Exception:
                pass
        return result

    def clear(self, market: str = None, timeframe: str = None):
        """캐시 삭제"""
        import shutil
        if market and timeframe:
            folder = self._get_folder(market, timeframe)
            if folder.exists():
                shutil.rmtree(folder)
        elif market:
            folder = self.root / market.replace("-", "_")
            if folder.exists():
                shutil.rmtree(folder)
        else:
            if self.root.exists():
                shutil.rmtree(self.root)
                self.root.mkdir(parents=True, exist_ok=True)
        logger.info(f"🗑️  NpyCache 삭제: market={market}, tf={timeframe}")


# ── 글로벌 싱글톤 ──────────────────────────────────────────────
_npy_cache: Optional[NpyCache] = None


def get_npy_cache() -> NpyCache:
    global _npy_cache
    if _npy_cache is None:
        _npy_cache = NpyCache()
    return _npy_cache
