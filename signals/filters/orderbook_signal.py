"""
APEX BOT - 호가창 신호 분석기 v2.0
페이크월(가짜 지지/저항벽) 탐지 강화
────────────────────────────────────
개선 사항:
  1. 페이크월 탐지: 벽 크기가 평균의 3배 이상인 단일 레벨 감지
  2. 벽 소멸 감지: 히스토리 비교로 갑작스러운 벽 사라짐 = 페이크월
  3. 벽 출현 속도: 갑자기 3배 이상 증가한 벽 = 조작 가능성
  4. 근접 레벨 필터: 최우선호가 5단계 이내만 유효한 벽으로 인정
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np
from loguru import logger


@dataclass
class OrderbookSignal:
    """호가창 분석 결과 (v2.0 — 페이크월 포함)"""
    market: str
    bid_ask_ratio: float
    spread_pct: float
    bid_wall_price: float
    ask_wall_price: float
    bid_concentration: float
    ask_concentration: float
    signal: str          # BUY_PRESSURE | SELL_PRESSURE | FAKE_WALL | NEUTRAL
    confidence: float
    reason: str
    # ── 페이크월 전용 필드 ──────────────────────────
    fake_wall_detected: bool = False
    fake_wall_side: str = ""        # "bid" | "ask"
    fake_wall_confidence: float = 0.0
    wall_vanished: bool = False     # 직전 벽이 갑자기 사라짐
    wall_size_ratio: float = 0.0    # 벽 크기 / 평균 크기 배율


class OrderbookSignalAnalyzer:
    """
    실시간 호가창 신호 분석기 v2.0
    ─────────────────────────────
    페이크월 판정 기준 (한국 시장 특화):
      1. 단일 레벨 크기 >= 평균 * WALL_THR (기본 3.0배)
      2. 최우선호가에서 5단계 이내 레벨만 유효
      3. 히스토리에서 1회만 등장 → 지속성 없음 → 페이크 가능성 높음
      4. 이전 스냅샷 대비 크기가 3배 이상 급증 → 조작 가능성
      5. 이전에 있던 벽이 30초 내 사라짐 → 페이크월 확정
    """

    BUY_PRESSURE_RATIO  = 1.5
    SELL_PRESSURE_RATIO = 1.5
    SPREAD_NORMAL_PCT   = 0.1
    SPREAD_WIDE_PCT     = 0.3
    TOP_N_LEVELS        = 5
    WALL_THR            = 3.0   # 평균 대비 몇 배 이상이면 벽으로 인정
    FAKE_WALL_VANISH_SEC = 30   # 이 시간 내 소멸 시 페이크월
    HISTORY_MAXLEN      = 8     # 히스토리 보관 스냅샷 수

    def __init__(self, wall_thr: float = 3.0, imbalance_thr: float = 0.3):
        self.WALL_THR = wall_thr
        self.imbalance_thr = imbalance_thr
        # 마켓별 벽 히스토리: {market: deque[(side, price, size, timestamp)]}
        self._wall_history: Dict[str, deque] = {}
        # 직전 스냅샷: {market: {"bids": [...], "asks": [...], "ts": float}}
        self._last_snapshot: Dict[str, dict] = {}

    def analyze(
        self, market: str, orderbook: Dict
    ) -> Optional[OrderbookSignal]:
        if not orderbook:
            return None
        try:
            units = orderbook.get("orderbook_units", [])
            if not units:
                return None

            bids = [
                (float(u.get("bid_price", 0)), float(u.get("bid_size", 0)))
                for u in units if u.get("bid_price", 0) > 0
            ]
            asks = [
                (float(u.get("ask_price", 0)), float(u.get("ask_size", 0)))
                for u in units if u.get("ask_price", 0) > 0
            ]
            if not bids or not asks:
                return None

            # ── 기본 지표 계산 ───────────────────────────────────
            best_bid   = bids[0][0]
            best_ask   = asks[0][0]
            spread     = (best_ask - best_bid) / best_ask * 100
            top_bids   = bids[:self.TOP_N_LEVELS]
            top_asks   = asks[:self.TOP_N_LEVELS]
            total_bids = sum(b[0] * b[1] for b in bids)
            total_asks = sum(a[0] * a[1] for a in asks)
            if total_bids < 1e-10 or total_asks < 1e-10:
                return None
            top_bid_vol = sum(b[0] * b[1] for b in top_bids)
            top_ask_vol = sum(a[0] * a[1] for a in top_asks)
            bid_ask_ratio    = total_bids / total_asks
            bid_concentration = top_bid_vol / (total_bids + 1e-10)
            ask_concentration = top_ask_vol / (total_asks + 1e-10)
            bid_wall = max(top_bids, key=lambda x: x[0] * x[1])
            ask_wall = max(top_asks, key=lambda x: x[0] * x[1])

            # ── 페이크월 탐지 ─────────────────────────────────────
            fake_detected, fake_side, fake_conf, wall_vanished, wall_ratio =                 self._detect_fake_wall(market, bids, asks)

            # ── 신호 결정 ─────────────────────────────────────────
            if fake_detected and fake_conf >= 0.6:
                signal     = "FAKE_WALL"
                confidence = fake_conf
                reason     = (
                    f"페이크월 탐지 ({fake_side.upper()}) "
                    f"신뢰도={fake_conf:.0%} 크기={wall_ratio:.1f}배"
                )
            else:
                signal, confidence, reason = self._classify(
                    bid_ask_ratio, spread, bid_concentration, ask_concentration
                )

            result = OrderbookSignal(
                market=market,
                bid_ask_ratio=round(bid_ask_ratio, 3),
                spread_pct=round(spread, 4),
                bid_wall_price=bid_wall[0],
                ask_wall_price=ask_wall[0],
                bid_concentration=round(bid_concentration, 3),
                ask_concentration=round(ask_concentration, 3),
                signal=signal,
                confidence=confidence,
                reason=reason,
                fake_wall_detected=fake_detected,
                fake_wall_side=fake_side,
                fake_wall_confidence=fake_conf,
                wall_vanished=wall_vanished,
                wall_size_ratio=wall_ratio,
            )

            # 히스토리 업데이트
            self._update_history(market, bids, asks)

            if fake_detected:
                logger.info(
                    f"🚨 페이크월 탐지 | {market} | {fake_side.upper()} | "
                    f"신뢰도={fake_conf:.0%} | 크기={wall_ratio:.1f}배 | "
                    f"소멸감지={wall_vanished}"
                )
            else:
                logger.debug(
                    f"📊 호가창 신호 | {market} | {signal} | "
                    f"bid/ask={bid_ask_ratio:.2f} | spread={spread:.3f}%"
                )
            return result

        except Exception as e:
            logger.error(f"호가창 분석 오류 ({market}): {e}")
            return None

    # ── 페이크월 핵심 탐지 로직 ────────────────────────────────────
    def _detect_fake_wall(
        self, market: str,
        bids: List[Tuple], asks: List[Tuple]
    ) -> Tuple[bool, str, float, bool, float]:
        """
        Returns: (detected, side, confidence, vanished, size_ratio)
        """
        fake_detected = False
        fake_side     = ""
        fake_conf     = 0.0
        wall_vanished = False
        wall_ratio    = 0.0

        for side, levels in [("bid", bids), ("ask", asks)]:
            if len(levels) < 3:
                continue
            sizes = [s for _, s in levels]
            avg_size = sum(sizes) / len(sizes) if sizes else 1e-9

            # 조건 1: 크기가 평균의 WALL_THR배 이상이고 상위 5단계 이내
            wall_candidates = [
                (idx, price, size)
                for idx, (price, size) in enumerate(levels)
                if size >= avg_size * self.WALL_THR and idx < 5
            ]
            if not wall_candidates:
                continue

            # 가장 큰 벽 선택
            best = max(wall_candidates, key=lambda x: x[2])
            idx, price, size = best
            ratio = size / (avg_size + 1e-9)

            conf = 0.0

            # 조건 2: 히스토리에서 지속성 확인
            history = self._wall_history.get(market, deque())
            same_side_history = [
                e for e in history
                if e[0] == side and abs(e[1] - price) / (price + 1e-9) < 0.005
            ]
            if len(same_side_history) <= 1:
                conf += 0.40   # 처음 등장 또는 1회만 → 지속성 없음

            # 조건 3: 최우선호가 바로 옆 (0~1단계) → 심리적 장벽 조작
            if idx <= 1:
                conf += 0.25

            # 조건 4: 직전 스냅샷 대비 갑자기 3배 이상 증가
            snap = self._last_snapshot.get(market)
            if snap:
                prev_levels = snap["bids"] if side == "bid" else snap["asks"]
                prev_same = [s for p, s in prev_levels if abs(p - price) / (price + 1e-9) < 0.002]
                if prev_same and size / (prev_same[0] + 1e-9) >= 3.0:
                    conf += 0.20   # 갑작스러운 급증

            # 조건 5: 직전 히스토리에 있던 벽이 현재 없으면 소멸 감지
            if history:
                last = list(history)[-1]
                last_side, last_price, last_size, last_ts = last
                elapsed = time.time() - last_ts
                if (last_side == side and
                        abs(last_price - price) / (price + 1e-9) > 0.005 and
                        elapsed < self.FAKE_WALL_VANISH_SEC):
                    wall_vanished = True
                    conf += 0.15

            conf = min(conf, 1.0)

            if conf > fake_conf:
                fake_conf     = conf
                fake_side     = side
                wall_ratio    = ratio
                fake_detected = conf >= 0.5

        return fake_detected, fake_side, fake_conf, wall_vanished, wall_ratio

    def _update_history(self, market: str, bids: List, asks: List):
        """벽 히스토리 업데이트"""
        if market not in self._wall_history:
            self._wall_history[market] = deque(maxlen=self.HISTORY_MAXLEN)
        now = time.time()
        for side, levels in [("bid", bids), ("ask", asks)]:
            if not levels:
                continue
            sizes = [s for _, s in levels]
            avg_size = sum(sizes) / len(sizes) if sizes else 1e-9
            walls = [
                (idx, p, s) for idx, (p, s) in enumerate(levels)
                if s >= avg_size * self.WALL_THR and idx < 5
            ]
            if walls:
                best = max(walls, key=lambda x: x[2])
                self._wall_history[market].append(
                    (side, best[1], best[2], now)
                )
        self._last_snapshot[market] = {
            "bids": bids, "asks": asks, "ts": now
        }

    def _classify(
        self,
        bid_ask_ratio: float,
        spread_pct: float,
        bid_conc: float,
        ask_conc: float,
    ) -> Tuple[str, float, str]:
        reasons = []
        if spread_pct > self.SPREAD_WIDE_PCT:
            return "NEUTRAL", 0.3, f"스프레드 과다 {spread_pct:.3f}%"
        confidence = 0.5
        if bid_ask_ratio >= self.BUY_PRESSURE_RATIO:
            boost = min((bid_ask_ratio - self.BUY_PRESSURE_RATIO) * 0.2, 0.3)
            confidence = min(0.5 + boost + bid_conc * 0.2, 0.90)
            reasons.append(f"매수벽 {bid_ask_ratio:.1f}배 우세")
            if bid_conc > 0.6:
                reasons.append(f"상위5단계 집중 {bid_conc:.0%}")
            return "BUY_PRESSURE", confidence, " | ".join(reasons)
        elif 1 / bid_ask_ratio >= self.SELL_PRESSURE_RATIO:
            inv_ratio = 1 / bid_ask_ratio
            boost = min((inv_ratio - self.SELL_PRESSURE_RATIO) * 0.2, 0.3)
            confidence = min(0.5 + boost + ask_conc * 0.2, 0.90)
            reasons.append(f"매도벽 {inv_ratio:.1f}배 우세")
            if ask_conc > 0.6:
                reasons.append(f"상위5단계 집중 {ask_conc:.0%}")
            return "SELL_PRESSURE", confidence, " | ".join(reasons)
        return "NEUTRAL", 0.5, f"bid/ask={bid_ask_ratio:.2f} (중립)"

    def get_confidence_adjustment(
        self, signal: Optional[OrderbookSignal], trade_side: str = "BUY"
    ) -> float:
        if signal is None:
            return 0.0
        # 페이크월 탐지 시 매수 강력 억제
        if signal.fake_wall_detected and trade_side == "BUY":
            return -0.20
        if trade_side == "BUY":
            if signal.signal == "BUY_PRESSURE":
                return min((signal.confidence - 0.5) * 0.3, 0.15)
            elif signal.signal == "SELL_PRESSURE":
                return max(-(signal.confidence - 0.5) * 0.3, -0.15)
        elif trade_side == "SELL":
            if signal.signal == "SELL_PRESSURE":
                return min((signal.confidence - 0.5) * 0.3, 0.15)
            elif signal.signal == "BUY_PRESSURE":
                return max(-(signal.confidence - 0.5) * 0.3, -0.15)
        return 0.0

    def can_buy(
        self, signal: Optional[OrderbookSignal]
    ) -> Tuple[bool, str]:
        if signal is None:
            return True, "호가창 데이터 없음 (통과)"
        # 페이크월 탐지 → 매수 차단
        if signal.fake_wall_detected and signal.fake_wall_confidence >= 0.6:
            return False, (
                f"페이크월 차단 ({signal.fake_wall_side.upper()}) "
                f"신뢰도={signal.fake_wall_confidence:.0%}"
            )
        # 벽 소멸 감지 → 스마트머니 이탈 가능성
        if signal.wall_vanished:
            return False, "벽 소멸 감지 → 스마트머니 이탈 가능"
        if signal.spread_pct > self.SPREAD_WIDE_PCT:
            return False, f"스프레드 과다 {signal.spread_pct:.3f}%"
        if signal.signal == "SELL_PRESSURE" and signal.confidence >= 0.75:
            return False, f"강한 매도 압력 (신뢰도={signal.confidence:.2f})"
        return True, "OK"
