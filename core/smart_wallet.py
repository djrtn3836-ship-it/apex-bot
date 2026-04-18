from __future__ import annotations
"""SmartWallet v1.0

  =   .
  Layer 0/1/2    .

Layer 0 (HOLD)  : .  .
Layer 1 (DUST)  : .    .
                      SELLABLE .
                      bot  .
Layer 2 (BOT)   :    .
                         .

 :
  -  ""  " " 
  -       (FIFO )
  -"""

import time
import json
from enum import Enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from loguru import logger

UPBIT_MIN_KRW  = 5_000
UPBIT_DEAD_KRW =   500
DUST_EXPIRE_DAYS = 30    # 이 일수 이상 방치된 짜투리 → 고아 포지션


# ══════════════════════════════════════════════════════════════
#  데이터 구조
# ══════════════════════════════════════════════════════════════

class LayerType(Enum):
    HOLD = "HOLD"
    DUST = "DUST"
    BOT  = "BOT"
    DEAD = "DEAD"


class DustState(Enum):
    PENDING    = "PENDING"     # 매도 불가, 합산 대기
    SELLABLE   = "SELLABLE"    # 가격 상승으로 단독 매도 가능해진 짜투리
    ORPHAN     = "ORPHAN"      # 30일 이상 방치 → 보고서 기록


@dataclass
class BotTransaction:
    """BotTransaction 클래스"""
    tx_id    : str
    qty      : float
    price    : float
    timestamp: float = field(default_factory=time.time)
    sold_qty : float = 0.0           # 이 TX에서 얼마나 팔렸는지

    @property
    def remaining_qty(self) -> float:
        return max(0.0, self.qty - self.sold_qty)


@dataclass
class CoinWallet:
    """.
    Layer 0/1/2   ."""
    symbol: str

    # Layer 0: HOLD
    hold_qty      : float = 0.0

    # Layer 1: DUST
    dust_qty      : float = 0.0
    dust_avg_price: float = 0.0
    dust_state    : DustState = DustState.PENDING
    dust_since    : float = field(default_factory=time.time)

    # Layer 2: BOT (트랜잭션 리스트)
    transactions  : list[BotTransaction] = field(default_factory=list)

    # 플래그
    is_hold       : bool = False   # HOLD 지정 여부

    # ── 집계 프로퍼티 ──────────────────────────────────────────────
    @property
    def bot_qty(self) -> float:
        return sum(tx.remaining_qty for tx in self.transactions)

    @property
    def bot_avg_price(self) -> float:
        txs = [tx for tx in self.transactions if tx.remaining_qty > 0]
        if not txs:
            return 0.0
        total_qty = sum(tx.remaining_qty for tx in txs)
        total_cost= sum(tx.remaining_qty * tx.price for tx in txs)
        return total_cost / total_qty if total_qty > 0 else 0.0

    @property
    def total_sellable_qty(self) -> float:
        """(BOT + DUST )"""
        return self.bot_qty + (
            self.dust_qty if self.dust_state != DustState.PENDING
            or self.bot_qty > 0    # 봇 포지션 있으면 항상 합산
            else 0.0
        )

    def dust_value(self, current_price: float) -> float:
        return self.dust_qty * current_price

    def bot_value(self, current_price: float) -> float:
        return self.bot_qty * current_price

    def total_value(self, current_price: float) -> float:
        return (self.hold_qty + self.dust_qty + self.bot_qty) * current_price

    # ── 짜투리 상태 자동 업그레이드 ──────────────────────────────
    def refresh_dust_state(self, current_price: float):
        if self.dust_qty < 1e-10:
            return
        val  = self.dust_qty * current_price
        days = (time.time() - self.dust_since) / 86400

        if days >= DUST_EXPIRE_DAYS:
            if self.dust_state != DustState.ORPHAN:
                self.dust_state = DustState.ORPHAN
                logger.warning(
                    f" {self.symbol}:  {self.dust_qty:.8f} "
                    f"→ {days:.0f}  →   "
                )
        elif val >= UPBIT_MIN_KRW and self.dust_state == DustState.PENDING:
            self.dust_state = DustState.SELLABLE
            logger.info(
                f"  {self.symbol}:  ₩{val:,.0f} ≥ ₩{UPBIT_MIN_KRW:,} "
                f"→ SELLABLE "
            )

    def __repr__(self):
        return (
            f"CoinWallet({self.symbol} | "
            f"hold={self.hold_qty:.6f} | "
            f"dust={self.dust_qty:.6f}[{self.dust_state.value}] | "
            f"bot={self.bot_qty:.6f} | "
            f"txs={len(self.transactions)})"
        )


# ══════════════════════════════════════════════════════════════
#  스마트 청산 전략 결정기
# ══════════════════════════════════════════════════════════════

class SmartSellDecider:
    """+   
        ."""

    @staticmethod
    def decide(
        wallet       : CoinWallet,
        current_price: float,
        confidence   : float,        # 0.0 ~ 1.0 매도 신호 강도
    ) -> dict:
        """Returns:
            {
              "ok"        : bool,
              "qty"       : float,    #   
              "strategy"  : str,      #  
              "note"      : str,
              "includes_dust": bool,
            }"""
        bot_qty  = wallet.bot_qty
        dust_qty = wallet.dust_qty
        hold_qty = wallet.hold_qty

        # ── HOLD 단독 → 차단 ──────────────────────────────────
        if wallet.is_hold and bot_qty < 1e-10:
            return {
                "ok": False, "qty": 0.0,
                "strategy": "HOLD_BLOCK",
                "note": "🔒 HOLD 코인, BOT 수량 없음 → 차단",
                "includes_dust": False,
            }

        # ── BOT 수량 없음 → 매도 불가 ─────────────────────────
        if bot_qty < 1e-10:
            return {
                "ok": False, "qty": 0.0,
                "strategy": "NO_BOT_QTY",
                "note": "BOT 보유 수량 없음",
                "includes_dust": False,
            }

        # ── 짜투리 합산 여부 결정 ─────────────────────────────
        include_dust = False
        dust_note    = ""

        if dust_qty > 1e-10:
            dust_val = dust_qty * current_price
            if dust_val < UPBIT_DEAD_KRW:
                dust_note    = f"짜투리 ₩{dust_val:,.0f} 사망잔고 → 제외"
                include_dust = False
            else:
                include_dust = True
                dust_note    = (
                    f"짜투리 {dust_qty:.8f}개(₩{dust_val:,.0f}) 합산 ✅"
                )

        # ── 신호 강도별 청산 전략 ─────────────────────────────
        if confidence >= 0.80:
            # STRONG: 전량 청산 (dust 포함)
            qty      = bot_qty + (dust_qty if include_dust else 0.0)
            strategy = "FULL_EXIT"
            note     = f"강한 매도 신호({confidence:.0%}) → 전량 청산"

        elif confidence >= 0.55:
            # MEDIUM: 50% 청산, dust는 합산
            qty      = bot_qty * 0.5 + (dust_qty if include_dust else 0.0)
            strategy = "HALF_EXIT"
            note     = f"중간 신호({confidence:.0%}) → 50% 청산"

        elif confidence >= 0.35:
            # WEAK: 25% 청산, dust 제외 (기다림)
            qty           = bot_qty * 0.25
            strategy      = "PARTIAL_EXIT"
            note          = f"약한 신호({confidence:.0%}) → 25% 청산"
            include_dust  = False
            dust_note     = "약한 신호 → 짜투리 보류 (더 좋은 기회 대기)"

        else:
            # TOO_WEAK: 청산 안 함
            return {
                "ok": False, "qty": 0.0,
                "strategy": "HOLD_SIGNAL",
                "note": f"신호 너무 약함({confidence:.0%}) → 보류",
                "includes_dust": False,
            }

        # ── 최소 주문금액 체크 ────────────────────────────────
        value_krw = qty * current_price
        if value_krw < UPBIT_MIN_KRW:
            # 부족하면 dust 강제 합산해서 채우기 시도
            if dust_qty > 1e-10 and not include_dust:
                qty          += dust_qty
                include_dust  = True
                value_krw     = qty * current_price
                dust_note     = "최소금액 부족 → 짜투리 강제 합산"

            if value_krw < UPBIT_MIN_KRW:
                return {
                    "ok": False, "qty": 0.0,
                    "strategy": "MIN_ORDER_FAIL",
                    "note": (
                        f"₩{value_krw:,.0f} < ₩{UPBIT_MIN_KRW:,} "
                        f"→ 매도 보류"
                    ),
                    "includes_dust": False,
                }

        full_note = f"{note} | {dust_note}" if dust_note else note

        return {
            "ok"           : True,
            "qty"          : qty,
            "strategy"     : strategy,
            "note"         : full_note,
            "includes_dust": include_dust,
            "value_krw"    : value_krw,
            "confidence"   : confidence,
        }


# ══════════════════════════════════════════════════════════════
#  SmartWalletManager  — 전체 지갑 관리자
# ══════════════════════════════════════════════════════════════

class SmartWalletManager:
    """.
    engine.py     ."""
    HOLD_CONFIG = Path("config/hold_coins.json")

    def __init__(self):
        self._wallets : dict[str, CoinWallet] = {}
        self._hold_set: set[str] = set()
        self._decider  = SmartSellDecider()
        self._load_hold_config()
        logger.info(
            f" SmartWalletManager  | "
            f"HOLD ={sorted(self._hold_set) or '없음'}"
        )

    def _load_hold_config(self):
        if self.HOLD_CONFIG.exists():
            try:
                cfg = json.loads(
                    self.HOLD_CONFIG.read_text(encoding="utf-8")
                )
                self._hold_set = {
                    s.upper() for s in cfg.get("hold_coins", [])
                }
            except Exception as e:
                logger.warning(f"hold_coins.json  : {e}")

    # ═══════════════════════════════════════════════════════════
    # 봇 시작 시 실제 잔고 스캔
    # ═══════════════════════════════════════════════════════════
    def scan_balances(self, balances):
        # 타입 안전 처리: list 가 아니면 빈 스캔
        if not isinstance(balances, list):
            logger.warning(
                f"scan_balances: list , {type(balances).__name__} 수신 → 스킵"
            )
            return {}
        """.
              ."""
        logger.info("" * 65)
        logger.info("   SmartWallet  ")
        logger.info("" * 65)

        for b in balances:
            sym    = b.get("currency", "").upper()
            if sym in ("KRW", ""):
                continue

            qty    = float(b.get("balance", 0) or 0)
            avg_px = float(b.get("avg_buy_price", 0) or 0)
            cur_px = float(b.get("current_price", avg_px) or avg_px)
            val    = qty * cur_px

            if qty < 1e-10:
                continue

            is_hold = sym in self._hold_set

            # 기존 지갑이 있으면 (봇 매수 기록 보존하면서) 동기화
            if sym in self._wallets:
                wallet = self._wallets[sym]
                # 실거래 잔고 기반 드리프트 감지
                expected = wallet.hold_qty + wallet.dust_qty + wallet.bot_qty
                actual   = qty
                drift    = abs(actual - expected)
                if drift > 1e-6:
                    logger.warning(
                        f"  {sym}:    | "
                        f"={expected:.8f} ={actual:.8f} "
                        f"={drift:.8f} →  "
                    )
                    # 차이를 DUST layer 에 흡수
                    wallet.dust_qty = max(0.0, wallet.dust_qty + (actual - expected))
                continue

            # 신규 지갑 생성
            wallet = CoinWallet(symbol=sym, is_hold=is_hold)

            if is_hold:
                wallet.hold_qty = qty
                tag = f"🔒 HOLD     | {qty:.8f}개 → 봇 완전 차단"

            elif val <= UPBIT_DEAD_KRW:
                wallet.dust_qty      = qty
                wallet.dust_avg_price= avg_px
                wallet.dust_state    = DustState.ORPHAN
                tag = f"💀 DEAD     | ₩{val:,.0f} → 무시"

            else:
                wallet.dust_qty      = qty
                wallet.dust_avg_price= avg_px
                wallet.dust_state    = DustState.PENDING
                tag = (
                    f"🧹 DUST     | ₩{val:,.0f} | "
                    + ("단독매도 가능금액 (봇 매수 후 합산 청산)"
                       if val >= UPBIT_MIN_KRW
                       else "매도불가 (합산 대기)")
                )

            self._wallets[sym] = wallet
            logger.info(f"  {sym:>8} | {tag}")

        logger.info("" * 65)

    # ═══════════════════════════════════════════════════════════
    # 매수 전 체크
    # ═══════════════════════════════════════════════════════════
    def can_buy(self, symbol: str) -> tuple[bool, str]:
        """True  →  
        False → HOLD"""
        wallet = self._wallets.get(symbol)

        if wallet and wallet.is_hold and wallet.bot_qty < 1e-10:
            return False, f"🔒 {symbol}: HOLD 코인 → 매수 차단"

        if wallet and wallet.dust_qty > 1e-10:
            return True, (
                f"🧹 {symbol}: 짜투리 {wallet.dust_qty:.8f}개 존재 "
                f"→ 신규 매수 후 매도 시 자동 합산"
            )

        return True, f"{symbol}: 신규 매수 진행"

    # ═══════════════════════════════════════════════════════════
    # 매수 완료 후 기록
    # ═══════════════════════════════════════════════════════════
    def record_buy(self, symbol: str, qty: float, price: float):
        """record_buy 실행"""
        wallet = self._get_or_create(symbol)

        tx = BotTransaction(
            tx_id    = f"{symbol}_{int(time.time()*1000)}",
            qty      = qty,
            price    = price,
        )
        wallet.transactions.append(tx)

        # DUST → 합산 대기 상태 확정
        if wallet.dust_qty > 1e-10:
            wallet.dust_state = DustState.PENDING
            logger.info(
                f" {symbol}:  {wallet.dust_qty:.8f} "
                f"→      "
            )

        logger.info(
            f"   | {symbol} | TX#{tx.tx_id} | "
            f"+{qty:.8f} @ ₩{price:,.0f} | "
            f" bot={wallet.bot_qty:.8f} | "
            f"dust={wallet.dust_qty:.8f}"
        )

    # ═══════════════════════════════════════════════════════════
    # 매도 결정 (핵심)
    # ═══════════════════════════════════════════════════════════
    def get_sell_decision(
        self,
        symbol       : str,
        current_price: float,
        confidence   : float = 1.0,
    ) -> dict:
        """.  qty   .

        confidence:
            0.8  →   ( )
            0.55~0.8 → 50%  ( )
            0.35~0.55→ 25%  ( )
            0.35  →"""
        wallet = self._wallets.get(symbol)
        if wallet is None:
            return {
                "ok": False, "qty": 0.0,
                "note": f"{symbol} 지갑 없음",
                "strategy": "NO_WALLET",
                "includes_dust": False,
            }

        # 짜투리 상태 자동 업그레이드 체크
        wallet.refresh_dust_state(current_price)

        result = SmartSellDecider.decide(wallet, current_price, confidence)

        if result["ok"]:
            logger.info(
                f"   | {symbol} | "
                f"={result['qty']:.8f} | "
                f"={result['strategy']} | "
                f"{result['note']}"
            )
        else:
            logger.warning(
                f"   | {symbol} | {result['note']}"
            )

        return result

    # ═══════════════════════════════════════════════════════════
    # 매도 완료 후 기록 (FIFO 역순 소진)
    # ═══════════════════════════════════════════════════════════
    def record_sell(
        self,
        symbol        : str,
        sold_qty      : float,
        includes_dust : bool = False,
    ):
        """.
        FIFO :     ( )."""
        wallet = self._wallets.get(symbol)
        if wallet is None:
            return

        remaining = sold_qty

        # 짜투리 먼저 차감 (공짜 수량 우선 소진)
        if includes_dust and wallet.dust_qty > 0:
            dust_used        = min(remaining, wallet.dust_qty)
            wallet.dust_qty  = max(0.0, wallet.dust_qty - dust_used)
            remaining       -= dust_used
            logger.info(
                f" {symbol}:  {dust_used:.8f}  "
            )

        # BOT 트랜잭션 FIFO 역순 차감
        for tx in reversed(wallet.transactions):
            if remaining < 1e-10:
                break
            use            = min(remaining, tx.remaining_qty)
            tx.sold_qty   += use
            remaining     -= use

        # 완전 소진된 TX 제거
        wallet.transactions = [
            tx for tx in wallet.transactions
            if tx.remaining_qty > 1e-10
        ]

        # 지갑 완전 청산
        if wallet.bot_qty < 1e-10 and wallet.dust_qty < 1e-10:
            self._wallets.pop(symbol, None)
            logger.info(f"  {symbol}:   ")
        else:
            logger.info(
                f" {symbol}:    | "
                f"bot={wallet.bot_qty:.8f} | "
                f"dust={wallet.dust_qty:.8f}"
            )

    # ═══════════════════════════════════════════════════════════
    # 유틸리티
    # ═══════════════════════════════════════════════════════════
    def _get_or_create(self, symbol: str) -> CoinWallet:
        if symbol not in self._wallets:
            self._wallets[symbol] = CoinWallet(
                symbol  = symbol,
                is_hold = symbol in self._hold_set,
            )
        return self._wallets[symbol]

    def get_wallet(self, symbol: str) -> Optional[CoinWallet]:
        return self._wallets.get(symbol)

    def get_orphan_report(self) -> list[dict]:
        """30"""
        return [
            {
                "symbol"    : w.symbol,
                "dust_qty"  : w.dust_qty,
                "dust_since": w.dust_since,
                "days"      : (time.time() - w.dust_since) / 86400,
            }
            for w in self._wallets.values()
            if w.dust_state == DustState.ORPHAN and w.dust_qty > 1e-10
        ]

    def print_status(self, current_prices: dict[str, float] | None = None):
        prices = current_prices or {}
        logger.info("" * 65)
        logger.info("   SmartWallet  ")
        logger.info("" * 65)
        for sym, w in self._wallets.items():
            px  = prices.get(sym, 0)
            val = w.total_value(px) if px else 0
            logger.info(
                f"  {sym:>8} | "
                f"HOLD={w.hold_qty:.6f} | "
                f"DUST={w.dust_qty:.6f}[{w.dust_state.value}] | "
                f"BOT={w.bot_qty:.6f}(TX:{len(w.transactions)}개) | "
                + (f"평가≈₩{val:,.0f}" if val else "")
            )
        orphans = self.get_orphan_report()
        if orphans:
            logger.warning(
                f"     {len(orphans)}개 → "
                f"reports/orphan_dust.json 확인"
            )
        logger.info("" * 65)
