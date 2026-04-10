# data/processors/orderbook_analyzer.py
"""(OrderBook Analyzer)
– / ,  ,  ,  
–"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger


@dataclass
class OrderBookSignal:
    market: str = ""
    buy_wall_price: float = 0.0
    sell_wall_price: float = 0.0
    imbalance: float = 0.0          # +1.0=완전 매수우세, -1.0=완전 매도우세
    spread_pct: float = 0.0
    pressure: str = "NEUTRAL"       # BUY / SELL / NEUTRAL
    spoofing_detected: bool = False
    wall_breakout: bool = False
    confidence_adj: float = 0.0
    reason: str = ""


class OrderBookAnalyzer:
    """–"""

    def __init__(
        self,
        wall_threshold: float = 3.0,
        imbalance_threshold: float = 0.3,
        spoof_ratio: float = 5.0,
    ):
        self.wall_threshold = wall_threshold
        self.imbalance_threshold = imbalance_threshold
        self.spoof_ratio = spoof_ratio
        self._prev_walls: dict[str, float] = {}
        logger.info(
            f" OrderBookAnalyzer  | "
            f"wall_thr={wall_threshold} imbalance_thr={imbalance_threshold}"
        )

    # ── 메인 분석 진입점 ────────────────────────────────────────────────
    def analyze(self, market: str, ob_data: Optional[dict]) -> OrderBookSignal:
        sig = OrderBookSignal(market=market)
        if not ob_data:
            sig.reason = "no_data"
            return sig

        try:
            bids = ob_data.get("orderbook_units", [])
            if not bids:
                sig.reason = "empty_units"
                return sig

            bid_prices = [u["bid_price"] for u in bids if u.get("bid_price")]
            ask_prices = [u["ask_price"] for u in bids if u.get("ask_price")]
            bid_sizes  = [u["bid_size"]  for u in bids if u.get("bid_size")]
            ask_sizes  = [u["ask_size"]  for u in bids if u.get("ask_size")]

            if not bid_prices or not ask_prices:
                sig.reason = "no_price"
                return sig

            total_bid = sum(p * s for p, s in zip(bid_prices, bid_sizes))
            total_ask = sum(p * s for p, s in zip(ask_prices, ask_sizes))
            total     = total_bid + total_ask

            # 불균형 지수
            sig.imbalance = (total_bid - total_ask) / total if total > 0 else 0.0

            # 스프레드
            best_bid = bid_prices[0]
            best_ask = ask_prices[0]
            sig.spread_pct = (best_ask - best_bid) / best_bid * 100 if best_bid > 0 else 0.0

            # 매수/매도 압력
            if sig.imbalance > self.imbalance_threshold:
                sig.pressure = "BUY"
            elif sig.imbalance < -self.imbalance_threshold:
                sig.pressure = "SELL"
            else:
                sig.pressure = "NEUTRAL"

            # 매수벽/매도벽 감지
            avg_bid_size = sum(bid_sizes) / len(bid_sizes) if bid_sizes else 0
            avg_ask_size = sum(ask_sizes) / len(ask_sizes) if ask_sizes else 0
            for price, size in zip(bid_prices, bid_sizes):
                if avg_bid_size > 0 and size > avg_bid_size * self.wall_threshold:
                    sig.buy_wall_price = price
                    break
            for price, size in zip(ask_prices, ask_sizes):
                if avg_ask_size > 0 and size > avg_ask_size * self.wall_threshold:
                    sig.sell_wall_price = price
                    break

            # 스푸핑 감지 (매수벽이 갑자기 사라짐)
            prev_wall = self._prev_walls.get(market, 0.0)
            if prev_wall > 0 and sig.buy_wall_price == 0.0:
                sig.spoofing_detected = True
                sig.reason += " spoofing_detected"
            self._prev_walls[market] = sig.buy_wall_price

            # 벽 돌파 감지
            if prev_wall > 0 and best_bid > prev_wall * 1.001:
                sig.wall_breakout = True
                sig.reason += " wall_breakout"

            # 신뢰도 조정값 계산
            sig.confidence_adj = sig.imbalance * 0.15
            if sig.spoofing_detected:
                sig.confidence_adj -= 0.10
            if sig.wall_breakout:
                sig.confidence_adj += 0.10

            sig.reason = sig.reason.strip() or "ok"
            logger.debug(
                f"   | {market} | "
                f"imbalance={sig.imbalance:.2f} | pressure={sig.pressure} | "
                f"spread={sig.spread_pct:.3f}% | wall_b={sig.buy_wall_price:.0f}"
            )

        except Exception as e:
            sig.reason = f"error:{e}"
            logger.warning(f" OrderBook   ({market}): {e}")

        return sig

    # ── 매수 가능 여부 판단 ──────────────────────────────────────────────
    def can_buy(self, sig: OrderBookSignal) -> tuple[bool, str]:
        if sig.spoofing_detected:
            return False, f"스푸핑 감지 ({sig.market})"
        if sig.pressure == "SELL" and sig.imbalance < -0.5:
            return False, f"강한 매도 압력 ({sig.market}) imbalance={sig.imbalance:.2f}"
        return True, "ok"

    # ── 신뢰도 조정값 반환 ──────────────────────────────────────────────
    def get_confidence_adjustment(
        self, sig: OrderBookSignal, trade_side: str = "BUY"
    ) -> float:
        if trade_side == "BUY":
            return sig.confidence_adj
        return -sig.confidence_adj
