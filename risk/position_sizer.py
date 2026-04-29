# risk/position_sizer.py — Kelly Criterion 동적 포지션 사이징 [PHASE3-v2]
"""Half-Kelly Criterion + 레짐 보정 + 7일 Rolling 윈도우
- DB 승률(W)/손익비(R) 기반 동적 Kelly 계산
- 데이터 부족 시 신뢰도 가중 Kelly (거래수 비례 점진 적용)
- 레짐별 포지션 크기 배수 자동 적용
- 7일 Rolling 윈도우로 최신 시장 반영
"""

import sqlite3
from pathlib import Path
from typing import Optional
from utils.logger import logger


class KellyPositionSizer:
    """Half-Kelly Criterion + GlobalRegime 보정
    - min_trades 미달 시 신뢰도 가중 Kelly (n/50 비율)
    - 레짐별 배수: BULL 1.30x / BEAR_WATCH 0.85x / BEAR 0.70x
    - 7일 rolling 윈도우로 최신 성과 반영
    """

    MIN_TRADES    = 10         # [PHASE3] Kelly 활성화 최소 거래 수 (20→10)
    HALF_KELLY    = 0.5        # Half-Kelly 안전 계수
    TRUST_SCALE   = 50         # [PHASE3] 신뢰도 가중 기준 거래수 (n/50)
    MAX_RISK_PCT  = 0.20       # 단일 포지션 최대 20%
    MIN_RISK_PCT  = 0.05       # 최소 5%
    FIXED_RATIO   = 0.10       # 데이터 부족 시 고정 10%
    MIN_ORDER_KRW = 5_000      # 업비트 최소 주문금액
    ROLLING_DAYS  = 7          # [PHASE3] 7일 rolling 윈도우
    DB_PATH       = Path("database/apex_bot.db")

    # ── 전략별 보정 계수 [PHASE3-v2: 누적 데이터 기반] ──────────
    # 하드코딩 최소화 – Kelly가 자동 계산하므로 보조 역할만
    STRATEGY_MULTIPLIER = {
        "Bollinger_Squeeze": 1.2,   # 누적 83.3% 승률 → 공격적
        "ML_Ensemble":       1.2,   # 누적 100% 승률 (소샘플) → 공격적
        "BEAR_REVERSAL":     1.2,   # 역발상 매수 → 공격적
        "Order_Block":       1.1,   # 누적 75.8% 승률 → 표준 이상
        "RSI_Divergence":    1.0,   # 표준
        "MACD_Cross":        0.9,   # 누적 52.5% 승률 → 소폭 보수
        "SURGE_FASTENTRY":   1.0,   # [PHASE3] 1.0x 유지 (이중억제 방지)
        "VWAP_Reversion":    0.8,   # 55% 승률 → 보수적
        "OrderBlock_SMC":    1.0,
        "Vol_Breakout":      0.2,   # 비활성화 수준
        "VolBreakout":       0.2,
        "volatility_break":  0.2,
        "ml_signal":         1.0,
        "default":           1.0,
    }

    # ── 레짐별 포지션 크기 배수 [PHASE3 신규] ────────────────────
    # BEAR에서도 Bollinger/Order_Block은 수익 → 0.70x (0.60x 아님)
    REGIME_SIZE_MULTIPLIER = {
        "BULL":       1.30,   # 강세장 → 공격적
        "RECOVERY":   1.10,   # 회복장 → 소폭 확대
        "BEAR_WATCH": 0.85,   # 약세주의 → 축소
        "BEAR":       0.70,   # 약세장 → 방어적 (0.60→0.70)
        "UNKNOWN":    0.90,   # 불명 → 보수적
    }

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or self.DB_PATH
        self._cache: dict = {}
        self._cache_count: int = 0

    # ── 핵심 공개 메서드 ──────────────────────────────────────────
    def calculate(
        self,
        total_capital: float,
        strategy: str = "default",
        market: str = "",
        confidence: float = 0.5,
        global_regime=None,          # [PHASE3] 레짐 파라미터
    ) -> float:
        """포지션 크기 계산 (KRW), 최소 MIN_ORDER_KRW 보장"""
        if total_capital <= 0:
            return 0.0

        # 1. Kelly fraction (신뢰도 가중 포함)
        kelly_f, n_trades = self._get_kelly_fraction_with_count(strategy)

        # 2. 신뢰도 가중: 거래수 적을수록 Kelly 축소 (n/TRUST_SCALE)
        trust_weight = min(n_trades, self.TRUST_SCALE) / self.TRUST_SCALE
        kelly_f_weighted = self.FIXED_RATIO + (kelly_f - self.FIXED_RATIO) * trust_weight
        base_amount = total_capital * kelly_f_weighted

        # 3. ML confidence 보정 (0.7 ~ 1.3x)
        conf_multiplier = 0.7 + (confidence * 0.6)
        base_amount *= conf_multiplier

        # 4. 전략 배수
        strat_mul = self.STRATEGY_MULTIPLIER.get(
            strategy, self.STRATEGY_MULTIPLIER["default"]
        )
        base_amount *= strat_mul

        # 5. [PHASE3] 레짐 배수
        _gr_str = str(
            getattr(global_regime, "value", global_regime or "UNKNOWN")
        ).upper()
        regime_mul = self.REGIME_SIZE_MULTIPLIER.get(
            _gr_str, self.REGIME_SIZE_MULTIPLIER["UNKNOWN"]
        )
        base_amount *= regime_mul

        # 6. 캡/플로어
        max_amount = total_capital * self.MAX_RISK_PCT
        min_amount = max(total_capital * self.MIN_RISK_PCT, self.MIN_ORDER_KRW)
        amount = max(min_amount, min(base_amount, max_amount))

        logger.info(
            f"[Kelly] {strategy} {market} | "
            f"kelly={kelly_f_weighted:.4f}(n={n_trades},trust={trust_weight:.2f}) "
            f"conf={confidence:.2f} strat={strat_mul:.1f}x "
            f"regime={_gr_str}x{regime_mul:.2f} → \u20a9{amount:,.0f}"
        )
        return amount

    # ── Kelly fraction 계산 (거래수 반환 포함) ───────────────────
    def _get_kelly_fraction_with_count(self, strategy: str):
        """(kelly_f, n_trades) 반환"""
        self._cache_count += 1
        cache_key = f"{strategy}_full"
        if cache_key in self._cache and self._cache_count % 20 != 0:
            return self._cache[cache_key]

        try:
            stats = self._query_stats(strategy)
            n = stats["total"] if stats else 0

            if stats is None or n < self.MIN_TRADES:
                logger.debug(
                    f"[Kelly] {strategy}: 거래수 {n}건 < {self.MIN_TRADES} "
                    f"→ 고정 {self.FIXED_RATIO*100:.0f}% (신뢰도 가중 적용)"
                )
                result = (self.FIXED_RATIO, n)
                self._cache[cache_key] = result
                return result

            W = stats["win_rate"]
            R = stats["rr_ratio"]

            raw_kelly  = (W * R - (1 - W)) / R
            half_kelly = max(raw_kelly * self.HALF_KELLY, self.MIN_RISK_PCT)
            capped     = min(half_kelly, self.MAX_RISK_PCT)

            result = (capped, n)
            self._cache[cache_key] = result
            logger.info(
                f"[Kelly] {strategy}: W={W:.2f} R={R:.2f} n={n} "
                f"raw={raw_kelly:.4f} half={half_kelly:.4f} "
                f"→ capped={capped:.4f} ({capped*100:.2f}%)"
            )
            return result

        except Exception as e:
            logger.warning(f"[Kelly] 오류 ({strategy}): {e}")
            return (self.FIXED_RATIO, 0)

    # ── 하위 호환: 기존 _get_kelly_fraction 유지 ────────────────
    def _get_kelly_fraction(self, strategy: str) -> float:
        kelly_f, _ = self._get_kelly_fraction_with_count(strategy)
        return kelly_f

    # ── DB 승률/손익비 조회 (7일 Rolling) ───────────────────────
    def _query_stats(self, strategy: str) -> Optional[dict]:
        """SQLite trade_history 7일 rolling 윈도우 통계"""
        if not self.db_path.exists():
            return None

        con = sqlite3.connect(str(self.db_path))
        cur = con.cursor()
        try:
            if strategy != "default":
                cur.execute(
                    """
                    SELECT profit_rate FROM trade_history
                    WHERE side='SELL'
                      AND strategy=?
                      AND timestamp >= datetime('now', '-7 days')
                    ORDER BY id DESC LIMIT 100
                    """,
                    (strategy,),
                )
            else:
                cur.execute(
                    """
                    SELECT profit_rate FROM trade_history
                    WHERE side='SELL'
                      AND timestamp >= datetime('now', '-7 days')
                    ORDER BY id DESC LIMIT 100
                    """
                )
            rows = [r[0] for r in cur.fetchall()]

            # 7일 데이터 부족 시 전체 기간으로 폴백
            if len(rows) < self.MIN_TRADES:
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
        """Kelly 현황 요약"""
        lines = ["[Kelly Criterion 현황]"]
        for strat, val in self._cache.items():
            if isinstance(val, tuple):
                f, n = val
                lines.append(f"  {strat}: {f*100:.2f}% (n={n})")
            else:
                lines.append(f"  {strat}: {val*100:.2f}%")
        if not self._cache:
            lines.append(f"  데이터 부족 → 고정 {self.FIXED_RATIO*100:.0f}%")
        return "\n".join(lines)
