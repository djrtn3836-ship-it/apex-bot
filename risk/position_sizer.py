# risk/position_sizer.py — Kelly Criterion v2.1 (PHASE3-v3)
"""
변경 사항 (v2.0 → v2.1):
  - calculate()에 consec_loss, atr_ratio 파라미터 추가
  - MDD-L2(연속손실 축소) + ATR 고변동성 축소를 내부로 통합
  - 모든 축소 적용 후 MIN_ORDER_KRW 보장 (EB-3 사전 방지)
  - buy_ratio는 engine_buy.py 에서 적용하되 최종 하한 보장
  - 기존 파라미터 미전달 시 완전 하위 호환
"""

import sqlite3
from pathlib import Path
from typing import Optional
from utils.logger import logger


class KellyPositionSizer:
    """Half-Kelly Criterion + GlobalRegime + MDD + ATR 통합 포지션 사이징"""

    MIN_TRADES    = 10
    HALF_KELLY    = 0.5
    TRUST_SCALE   = 50
    MAX_RISK_PCT  = 0.20
    MIN_RISK_PCT  = 0.05
    FIXED_RATIO   = 0.10
    MIN_ORDER_KRW = 5_000
    ROLLING_DAYS  = 7
    DB_PATH       = Path("database/apex_bot.db")

    STRATEGY_MULTIPLIER = {
        "Bollinger_Squeeze": 1.2,
        "ML_Ensemble":       0.5,
        "BEAR_REVERSAL":     1.2,
        "OrderBlock_SMC":    0.7,   # [REFACTOR] Order_Block → OrderBlock_SMC
        "RSI_Divergence":    1.0,
        "MACD_Cross":        0.9,
        "SURGE_FASTENTRY":   1.0,
        "VWAP_Reversion":    0.8,
        "OrderBlock_SMC":    0.7,   # [BUG-7] 180일 승률 37.9% → 축소
        "VolBreakout":       0.2,   # [REFACTOR] Vol_Breakout 키 폐기
        "volatility_break":  0.2,
        "ml_signal":         1.0,
        "default":           0.8,   # [BUG-7] 기본값 보수화
    }

    REGIME_SIZE_MULTIPLIER = {
        "BULL":       1.30,
        "RECOVERY":   1.10,
        "BEAR_WATCH": 0.85,
        "BEAR":       0.70,
        "UNKNOWN":    0.90,
    }

    # MDD-L2: 연속손실별 축소 비율 (engine_buy.py 에서 이관)
    # 단계적 축소로 급격한 하락 방지
    _CONSEC_LOSS_MULT = {
        0: 1.00,
        1: 1.00,
        2: 0.80,   # 2연속 → 20% 축소
        3: 0.60,   # 3연속 → 40% 축소 (기존 50% → 완화)
        4: 0.50,   # 4연속 → 50% 축소
        5: 0.40,   # 5연속 이상 → 60% 축소
    }

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path    = db_path or self.DB_PATH
        self._cache: dict = {}
        self._cache_count: int = 0

    def calculate(
        self,
        total_capital:  float,
        strategy:       str   = "default",
        market:         str   = "",
        confidence:     float = 0.5,
        global_regime         = None,
        # ── v2.1 신규 파라미터 (하위 호환: 기본값 = 축소 없음) ──
        consec_loss:    int   = 0,     # 연속 손실 횟수
        atr_ratio:      float = 1.0,   # 현재ATR / 기준ATR (1.5 초과 시 축소)
        is_bear_reversal: bool = False, # BEAR_REVERSAL 플래그
    ) -> float:
        """포지션 크기 계산 (KRW).
        모든 보정 적용 후 MIN_ORDER_KRW × 2 이상 보장.
        보장 불가 시 0.0 반환 → engine_buy.py EB-3/EB-4 에서 차단.
        """
        if total_capital <= 0:
            return 0.0

        # ── Step 1: Kelly fraction ───────────────────────────────
        kelly_f, n_trades = self._get_kelly_fraction_with_count(strategy)

        # ── Step 2: 신뢰도 가중 ──────────────────────────────────
        trust_weight      = min(n_trades, self.TRUST_SCALE) / self.TRUST_SCALE
        kelly_f_weighted  = (
            self.FIXED_RATIO
            + (kelly_f - self.FIXED_RATIO) * trust_weight
        )
        base_amount = total_capital * kelly_f_weighted

        # ── Step 3: ML confidence 보정 (0.7 ~ 1.3×) ─────────────
        conf_mult   = 0.7 + (confidence * 0.6)
        base_amount *= conf_mult

        # ── Step 4: 전략 배수 ────────────────────────────────────
        strat_mul    = self.STRATEGY_MULTIPLIER.get(
            strategy, self.STRATEGY_MULTIPLIER["default"]
        )
        base_amount *= strat_mul

        # ── Step 5: 레짐 배수 ────────────────────────────────────
        _gr_str     = str(
            getattr(global_regime, "value", global_regime or "UNKNOWN")
        ).upper()
        regime_mul  = self.REGIME_SIZE_MULTIPLIER.get(
            _gr_str, self.REGIME_SIZE_MULTIPLIER["UNKNOWN"]
        )
        base_amount *= regime_mul

        # ── Step 6: MDD-L2 연속손실 단계적 축소 (이관) ───────────
        consec_key   = min(consec_loss, 5)
        consec_mult  = self._CONSEC_LOSS_MULT[consec_key]
        if consec_mult < 1.0:
            logger.info(
                f"[Kelly-MDD] {strategy} {market} | "
                f"연속손실 {consec_loss}회 → {consec_mult:.2f}× 축소"
            )
        base_amount *= consec_mult

        # ── Step 7: ATR 고변동성 축소 (이관) ────────────────────
        # atr_ratio = 현재ATR / 기준ATR
        # 1.5 초과 시 선형 축소 (최대 0.5× @ atr_ratio=3.0)
        if atr_ratio > 1.5:
            atr_mult    = max(0.5, 1.0 - (atr_ratio - 1.5) * 0.33)
            logger.debug(
                f"[Kelly-ATR] {strategy} {market} | "
                f"ATR배율={atr_ratio:.2f} → {atr_mult:.2f}× 축소"
            )
            base_amount *= atr_mult

        # ── Step 8: BEAR_REVERSAL 50% 축소 (이관) ────────────────
        if is_bear_reversal:
            base_amount *= 0.5
            logger.info(
                f"[Kelly-BR] {strategy} {market} | "
                f"BEAR_REVERSAL → 0.5× 축소"
            )

        # ── Step 9: 캡/플로어 ────────────────────────────────────
        max_amount  = total_capital * self.MAX_RISK_PCT
        min_amount  = max(total_capital * self.MIN_RISK_PCT, self.MIN_ORDER_KRW)
        amount      = max(min_amount, min(base_amount, max_amount))

        # ── Step 10: 최종 MIN_ORDER_KRW×2 보장 ───────────────────
        # buy_ratio(0.5~1.0) 적용 후에도 MIN_ORDER_KRW 이상이 되려면
        # 여기서 MIN_ORDER_KRW×2 이상이어야 함
        _hard_floor = self.MIN_ORDER_KRW * 2  # ₩10,000
        if amount < _hard_floor:
            if total_capital >= _hard_floor * 3:
                # 자본이 충분한데 계산값이 작은 경우 → 하드플로어 적용
                logger.info(
                    f"[Kelly-FLOOR] {strategy} {market} | "
                    f"계산값 ₩{amount:,.0f} < 하드플로어 ₩{_hard_floor:,} "
                    f"→ ₩{_hard_floor:,} 보정"
                )
                amount = float(_hard_floor)
            else:
                # 자본 자체가 부족 → 0 반환, engine_buy.py 에서 차단
                logger.debug(
                    f"[Kelly-ZERO] {strategy} {market} | "
                    f"자본 ₩{total_capital:,.0f} 부족 → 0 반환"
                )
                return 0.0

        logger.info(
            f"[Kelly] {strategy} {market} | "
            f"kelly={kelly_f_weighted:.4f}(n={n_trades},"
            f"trust={trust_weight:.2f}) "
            f"conf={confidence:.2f} strat={strat_mul:.1f}× "
            f"regime={_gr_str}×{regime_mul:.2f} "
            f"mdd={consec_mult:.2f}× atr={atr_ratio:.2f} "
            f"→ ₩{amount:,.0f}"
        )
        return amount

    # ── Kelly fraction 계산 ──────────────────────────────────────
    def _get_kelly_fraction_with_count(self, strategy: str):
        self._cache_count += 1
        cache_key = f"{strategy}_full"
        if cache_key in self._cache and self._cache_count % 20 != 0:
            return self._cache[cache_key]

        try:
            stats = self._query_stats(strategy)
            n     = stats["total"] if stats else 0

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
            if raw_kelly <= 0:
                result = (self.FIXED_RATIO, n)
                self._cache[cache_key] = result
                logger.debug(
                    f"[Kelly] {strategy}: raw_kelly={raw_kelly:.4f}<=0 "
                    f"→ FIXED {self.FIXED_RATIO*100:.0f}%"
                )
                return result

            half_kelly = max(raw_kelly * self.HALF_KELLY, self.MIN_RISK_PCT)
            capped     = min(half_kelly, self.MAX_RISK_PCT)
            result     = (capped, n)
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

    def _get_kelly_fraction(self, strategy: str) -> float:
        kelly_f, _ = self._get_kelly_fraction_with_count(strategy)
        return kelly_f

    # ── DB 통계 조회 ─────────────────────────────────────────────
    def _query_stats(self, strategy: str) -> Optional[dict]:
        if not self.db_path.exists():
            return None

        con = sqlite3.connect(str(self.db_path))
        cur = con.cursor()
        try:
            if strategy != "default":
                cur.execute(
                    """
                    SELECT profit_rate FROM trade_history
                    WHERE side='SELL' AND strategy=?
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

        wins    = [r for r in rows if r > 0]
        losses  = [r for r in rows if r < 0]
        total   = len(rows)
        W       = len(wins) / total if total else 0
        avg_win  = sum(wins)  / len(wins)   if wins   else 0.001
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0.001
        R        = avg_win / avg_loss if avg_loss > 0 else 1.0

        return {"total": total, "win_rate": W, "rr_ratio": R}

    def get_summary(self) -> str:
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
