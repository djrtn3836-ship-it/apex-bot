"""
APEX BOT - Slippage Model
호가창 스프레드 + 거래량 기반 슬리피지 예측
"""
import numpy as np
from loguru import logger


class SlippageModel:
    """
    실제 체결가 = 주문가 + 슬리피지
    슬리피지 요인:
      1. 호가 스프레드 (bid-ask spread)
      2. 주문 크기 대비 호가창 유동성
      3. 시장 변동성 (ATR 기반)
    """

    # 코인별 기본 슬리피지 (실측 기반, %)
    BASE_SLIPPAGE = {
        "KRW-BTC":  0.03,
        "KRW-ETH":  0.04,
        "KRW-SOL":  0.06,
        "KRW-XRP":  0.05,
        "KRW-ADA":  0.07,
        "KRW-DOGE": 0.08,
        "KRW-DOT":  0.07,
        "KRW-LINK": 0.07,
        "KRW-AVAX": 0.07,
        "KRW-ATOM": 0.08,
    }
    DEFAULT_SLIPPAGE = 0.08  # 미등록 코인 기본값

    def __init__(self):
        self._history: dict = {}  # 실측 슬리피지 누적
        logger.info("✅ SlippageModel 초기화 | 호가창 스프레드 기반 슬리피지 예측")

    def estimate(
        self,
        market: str,
        order_amount_krw: float,
        orderbook: dict = None,
        volatility: float = None,
    ) -> float:
        """
        슬리피지 예측 (%)
        - order_amount_krw: 주문 금액 (KRW)
        - orderbook: 호가창 데이터 (없으면 기본값 사용)
        - volatility: ATR 기반 변동성 (없으면 무시)
        반환: 예상 슬리피지 비율 (예: 0.05 = 0.05%)
        """
        base = self.BASE_SLIPPAGE.get(market, self.DEFAULT_SLIPPAGE)

        # 1. 호가창 스프레드 반영
        spread_adj = 0.0
        if orderbook:
            try:
                asks = orderbook.get("asks", [])
                bids = orderbook.get("bids", [])
                if asks and bids:
                    best_ask = float(asks[0][0])
                    best_bid = float(bids[0][0])
                    spread_pct = (best_ask - best_bid) / best_bid * 100
                    spread_adj = spread_pct * 0.5  # 스프레드의 50% 슬리피지
            except Exception:
                pass

        # 2. 주문 크기 반영 (클수록 슬리피지 증가)
        size_adj = 0.0
        if order_amount_krw > 500_000:
            size_adj = 0.02
        elif order_amount_krw > 200_000:
            size_adj = 0.01

        # 3. 변동성 반영
        vol_adj = 0.0
        if volatility is not None and volatility > 0:
            vol_adj = min(volatility * 0.1, 0.05)

        total = base + spread_adj + size_adj + vol_adj
        total = min(total, 0.5)  # 최대 0.5% 상한

        logger.debug(
            f"슬리피지 예측 ({market}): {total:.3f}% "
            f"[base={base:.3f} spread={spread_adj:.3f} "
            f"size={size_adj:.3f} vol={vol_adj:.3f}]"
        )
        return total

    def apply(self, price: float, market: str, side: str = "buy", **kwargs) -> float:
        """
        슬리피지 적용 가격 반환
        - side: "buy" → 가격 상승, "sell" → 가격 하락
        """
        slippage_pct = self.estimate(market, kwargs.get("order_amount_krw", 100_000))
        if side == "buy":
            return price * (1 + slippage_pct / 100)
        else:
            return price * (1 - slippage_pct / 100)

    def record_actual(self, market: str, expected: float, actual: float):
        """실제 체결 슬리피지 기록 (자기학습)"""
        if expected <= 0:
            return
        actual_slip = abs(actual - expected) / expected * 100
        if market not in self._history:
            self._history[market] = []
        self._history[market].append(actual_slip)
        # 최근 50건만 유지
        if len(self._history[market]) > 50:
            self._history[market].pop(0)
        # 실측 평균으로 BASE_SLIPPAGE 업데이트
        avg = float(np.mean(self._history[market]))
        self.BASE_SLIPPAGE[market] = round(avg, 4)
        logger.debug(f"슬리피지 실측 업데이트 ({market}): {avg:.4f}%")

    def get_status(self) -> dict:
        return {
            "base_slippage": self.BASE_SLIPPAGE,
            "history_count": {k: len(v) for k, v in self._history.items()},
        }
