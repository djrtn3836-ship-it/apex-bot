"""APEX BOT -    (Fear & Greed Index)
Alternative.me API →    

 :
  v1.1 - is_valid property +    
       - get_signal_adjustment() "mode"  
       - Extreme Greed 90+  block_buy  
       - get_buy_threshold_adjustment()"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional, Tuple
from loguru import logger

try:
    import aiohttp
    AIOHTTP_OK = True
except ImportError:
    AIOHTTP_OK = False

FEAR_GREED_API = "https://api.alternative.me/fng/?limit=2&format=json"
CACHE_TTL = 3600  # 1시간


class FearGreedMonitor:
    """:
        fgm = FearGreedMonitor()
        await fgm.fetch()

        adj = fgm.get_signal_adjustment()
        # adj: {
        #   "confidence_mult": 1.1,
        #   "position_ratio": 1.0,
        #   "block_buy": False,
        #   "mode": "extreme_fear" | "fear" | "neutral" | "greed" | "extreme_greed",
        #   "index": 13,
        #   "label": "Extreme Fear",
        #   "reason": "...",
        # }"""

    def __init__(self):
        self._index: Optional[int] = None
        self._label: str = "Unknown"
        self._prev_index: Optional[int] = None
        self._last_fetch: float = 0
        self._fetch_count: int = 0
        self._available = AIOHTTP_OK

        if not AIOHTTP_OK:
            logger.warning(" aiohttp  →   ")
        else:
            logger.info("    ")

    # ── Public API ──────────────────────────────────────────────

    @property
    def index(self) -> Optional[int]:
        return self._index

    @property
    def label(self) -> str:
        return self._label

    @property
    def is_valid(self) -> bool:
        """(1  ) — @property"""
        return (
            self._index is not None
            and time.time() - self._last_fetch < CACHE_TTL * 2
        )

    def is_valid_check(self) -> bool:
        """FIX: engine.py is_valid()      alias
        engine.py: fear_greed.is_valid() → TypeError"""
        return self.is_valid

    def get_signal_adjustment(self) -> Dict:
        """FIX: "mode"  , "block_buy"  
              

        Returns:
            {
              "confidence_mult": float,      (0.85 ~ 1.10)
              "position_ratio": float,        (0.60 ~ 1.00)
              "block_buy": bool,             
              "mode": str,                  
              "index": int | None,          
              "label": str,                
              "reason": str,              
            }"""
        base = {
            "confidence_mult": 1.0,
            "position_ratio": 1.0,
            "block_buy": False,
            "mode": "unknown",
            "index": self._index,
            "label": self._label,
            "reason": "공포탐욕 데이터 없음",
        }

        if not self.is_valid or self._index is None:
            return base

        idx = self._index

        if idx <= 25:        # Extreme Fear
            base.update({
                "confidence_mult": 1.10,
                "position_ratio":  1.00,
                "block_buy":       False,
                "mode":            "extreme_fear",
                "reason":          f"극단적 공포 ({idx}) → 역발상 매수 기회, 신뢰도 +10%",
            })
        elif idx <= 45:      # Fear
            base.update({
                "confidence_mult": 1.05,
                "position_ratio":  1.00,
                "block_buy":       False,
                "mode":            "fear",
                "reason":          f"공포 ({idx}) → 신뢰도 +5%",
            })
        elif idx <= 55:      # Neutral
            base.update({
                "confidence_mult": 1.00,
                "position_ratio":  1.00,
                "block_buy":       False,
                "mode":            "neutral",
                "reason":          f"중립 ({idx})",
            })
        elif idx <= 75:      # Greed
            base.update({
                "confidence_mult": 0.95,
                "position_ratio":  0.80,
                "block_buy":       False,
                "mode":            "greed",
                "reason":          f"탐욕 ({idx}) → 신뢰도 -5%, 포지션 80%",
            })
        elif idx <= 89:      # Extreme Greed (하위)
            base.update({
                "confidence_mult": 0.85,
                "position_ratio":  0.60,
                "block_buy":       False,
                "mode":            "extreme_greed",
                "reason":          f"극단적 탐욕 ({idx}) → 신뢰도 -15%, 포지션 60%",
            })
        else:                # Extreme Greed 90+ → 매수 차단
            base.update({
                "confidence_mult": 0.80,
                "position_ratio":  0.50,
                "block_buy":       True,
                "mode":            "suppressed",  # ✅ engine.py 체크 키
                "reason":          f"극단적 탐욕 90+ ({idx}) → 신규 매수 차단",
            })

        return base

    def get_buy_threshold_adjustment(self) -> float:
        """( ),    ( )

        Returns:
              (-1.0 ~ +2.0)"""
        if not self.is_valid or self._index is None:
            return 0.0

        idx = self._index
        if idx <= 25:   return -1.0    # 극단적 공포
        if idx <= 45:   return -0.5    # 공포
        if idx <= 55:   return  0.0    # 중립
        if idx <= 75:   return +0.5    # 탐욕
        return              +1.5       # 극단적 탐욕

    def is_trend_reversing(self) -> bool:
        """(20+)"""
        if self._index is None or self._prev_index is None:
            return False
        return abs(self._index - self._prev_index) >= 20

    def get_dashboard_info(self) -> Dict:
        """get_dashboard_info 실행"""
        if not self.is_valid:
            return {"index": None, "label": "N/A", "emoji": "⚪"}
        idx = self._index
        if idx <= 25:   emoji = "😱"
        elif idx <= 45: emoji = "😰"
        elif idx <= 55: emoji = "😐"
        elif idx <= 75: emoji = "😏"
        else:           emoji = "🤑"
        return {
            "index": idx,
            "label": self._label,
            "emoji": emoji,
            "prev":  self._prev_index,
        }

    # ── Async Fetch ─────────────────────────────────────────────

    async def fetch(self) -> bool:
        """API"""
        if not self._available:
            return False
        if time.time() - self._last_fetch < CACHE_TTL:
            return True
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(FEAR_GREED_API) as resp:
                    if resp.status != 200:
                        return False
                    data = await resp.json()
                    items = data.get("data", [])
                    if not items:
                        return False

                    today = items[0]
                    self._index = int(today.get("value", 50))
                    self._label = today.get("value_classification", "Unknown")
                    if len(items) > 1:
                        self._prev_index = int(items[1].get("value", 50))

                    self._last_fetch = time.time()
                    self._fetch_count += 1

                    adj = self.get_signal_adjustment()
                    logger.info(
                        f"  : {self._index} ({self._label}) | "
                        f"모드={adj['mode']} | {adj['reason']}"
                    )
                    return True
        except Exception as e:
            logger.debug(f"  : {e}")
            return False
