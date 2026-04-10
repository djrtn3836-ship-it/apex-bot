# risk/position_sizer.py  — Kelly Criterion 동적 포지션 사이징
"""Half-Kelly Criterion    
- (W) (R) DB    
- ( )    fallback
-   :  5% ( )
-  : 5,000 KRW ( )"""

import sqlite3
from pathlib import Path
from typing import Optional
from utils.logger import logger


class KellyPositionSizer:
    """Half-Kelly Criterion  
    - min_trades     Kelly 
    -   fixed_ratio"""

    MIN_TRADES       = 20        # Kelly 활성화 최소 거래 수
    HALF_KELLY       = 0.5       # Half-Kelly 안전 계수
    MAX_RISK_PCT = 0.20      # 단일 포지션 최대 20%
    MIN_RISK_PCT = 0.05      # 최소 5%  (너무 소액 방지)
    FIXED_RATIO  = 0.10      # 데이터 부족시 고정 10%
    MIN_ORDER_KRW    = 5_000     # 업비트 최소 주문금액
    DB_PATH          = Path("database/apex_bot.db")

    # ── 전략별 보정 계수 ──────────────────────────────────────
    STRATEGY_MULTIPLIER = {
        "BEAR_REVERSAL":     1.2,   # 역발상 매수 → 약간 공격적
        "OrderBlock_SMC":    1.0,
        "VWAP_Reversion":    0.9,
        "volatility_break":  1.1,
        "ml_signal":         1.3,   # ML 고신뢰 → 더 공격적
        "default":           1.0,
    }

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or self.DB_PATH
        self._cache: dict = {}          # {strategy: (W, R, kelly_f)}
        self._cache_count: int = 0      # 캐시 갱신 주기 카운터

    # ── 핵심 공개 메서드 ─────────────────────────────────────
    def calculate(
        self,
        total_capital: float,
        strategy: str = "default",
        market: str = "",
        confidence: float = 0.5,
    ) -> float:
        """Returns:   (KRW),  MIN_ORDER_KRW"""
        kelly_f = self._get_kelly_fraction(strategy)
        base_amount = total_capital * kelly_f

        # 신뢰도 보정 (ML confidence 반영)
        conf_multiplier = 0.7 + (confidence * 0.6)   # 0.7 ~ 1.3
        base_amount *= conf_multiplier

        # 전략 보정
        strat_mul = self.STRATEGY_MULTIPLIER.get(
            strategy, self.STRATEGY_MULTIPLIER["default"]
        )
        base_amount *= strat_mul

        # 캡/플로어 적용
        max_amount = total_capital * self.MAX_RISK_PCT
        min_amount = max(total_capital * self.MIN_RISK_PCT, self.MIN_ORDER_KRW)
        amount = max(min_amount, min(base_amount, max_amount))

        logger.info(
            f"[Kelly] {strategy} {market} | "
            f"kelly_f={kelly_f:.4f} conf={confidence:.2f} "
            f"strat_mul={strat_mul} → ₩{amount:,.0f}"
        )
        return amount

    # ── DB에서 승률/손익비 계산 ───────────────────────────────
    def _get_kelly_fraction(self, strategy: str) -> float:
        """DB    Half-Kelly f*"""
        # 캐시: 20거래마다 갱신
        self._cache_count += 1
        if strategy in self._cache and self._cache_count % 20 != 0:
            return self._cache[strategy]

        try:
            stats = self._query_stats(strategy)
            if stats is None or stats["total"] < self.MIN_TRADES:
                logger.debug(
                    f"[Kelly] {strategy}:   "
                    f"({stats['total'] if stats else 0}) "
                    f"→ 고정 {self.FIXED_RATIO*100:.0f}%"
                )
                return self.FIXED_RATIO

            W = stats["win_rate"]
            R = stats["rr_ratio"]

            # Kelly 공식
            raw_kelly = (W * R - (1 - W)) / R
            half_kelly = max(raw_kelly * self.HALF_KELLY, self.MIN_RISK_PCT)
            capped     = min(half_kelly, self.MAX_RISK_PCT)

            self._cache[strategy] = capped
            logger.info(
                f"[Kelly] {strategy}: W={W:.2f} R={R:.2f} "
                f"raw={raw_kelly:.4f} half={half_kelly:.4f} "
                f"→ capped={capped:.4f} ({capped*100:.2f}%)"
            )
            return capped

        except Exception as e:
            logger.warning(f"[Kelly]  ({strategy}): {e}")
            return self.FIXED_RATIO

    def _query_stats(self, strategy: str) -> Optional[dict]:
        """SQLite trade_history ·"""
        if not self.db_path.exists():
            return None

        con = sqlite3.connect(str(self.db_path))
        cur = con.cursor()
        try:
            # 전략 조건: 전략명 또는 전체 평균
            if strategy != "default":
                cur.execute(
                    """
                    SELECT profit_rate FROM trade_history
                    WHERE side='SELL' AND strategy=?
                    ORDER BY id DESC LIMIT 100
                    """,
                    (strategy,),
                )
            else:
                cur.execute(
                    """
                    SELECT profit_rate FROM trade_history
                    WHERE side='SELL'
                    ORDER BY id DESC LIMIT 100
                    """
                )
            rows = [r[0] for r in cur.fetchall()]
        finally:
            con.close()

        if not rows:
            return {"total": 0, "win_rate": 0, "rr_ratio": 1}

        wins   = [r for r in rows if r > 0]
        losses = [r for r in rows if r <= 0]
        total  = len(rows)
        W      = len(wins) / total if total else 0

        avg_win  = sum(wins)   / len(wins)   if wins   else 0.001
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0.001
        R        = avg_win / avg_loss if avg_loss > 0 else 1.0

        return {"total": total, "win_rate": W, "rr_ratio": R}

    def get_summary(self) -> str:
        """Kelly"""
        lines = ["[Kelly Criterion 현황]"]
        for strat, f in self._cache.items():
            lines.append(f"  {strat}: {f*100:.2f}%")
        if not self._cache:
            lines.append(f"  데이터 부족 → 전략 고정 {self.FIXED_RATIO*100:.0f}%")
        return "\n".join(lines)

