"""
core/surge_detector.py
─────────────────────────────────────────────────────────────
전문 급등 포착 엔진 v2.1.0  (Professional Quant Grade)

타임프레임 설계 원칙:
    진입 감지  : 1분봉  → 거래량 폭발, 전고점 돌파 (즉각 반응)
    추세 확인  : 15분봉 → 세력 매집 패턴 (시간 걸리는 신호)
    맥락 확인  : 5분봉  → MTF 정렬, BTC 역행
    실시간     : ticks  → 체결 강도 (ask_bid)
    실시간     : orderbook → OBI + 매수벽

7가지 독립 신호:
    1. VolumeBreakout      : 1분봉 거래량 폭발 + OBV
    2. TakerBuyRatio       : 체결 강도 (BID/ASK 비율)
    3. PriceBreakout       : 1분봉 전고점/52주 고점 돌파
    4. OrderbookImbalance  : OBI + 매수벽 레벨 + 스프레드
    5. BTCDecoupling       : 5분봉 BTC 역행 강도
    6. AccumulationPattern : 15분봉 세력 매집 (Wyckoff)
    7. MomentumAlignment   : 5분봉 RSI+MACD+OBV 삼중 정렬

펌프앤덤프 필터:
    - 1분봉 거래량 폭발 + 음봉 → 세력 매도 → 점수 70% 차감
    - 직전 캔들 폭발 후 현재 거래량 급감 → 덤프 직전 → 차단

시간대 가중치 (KST):
    활성  14:00~16:00, 22:00~02:00 → x1.20
    비활성 04:00~08:00              → x0.85

MTF 정렬 보너스:
    5분봉 + 15분봉 EMA20 모두 위에서 상승 → +0.08
─────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import numpy as np
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List
from loguru import logger


# ══════════════════════════════════════════════════════════════
# 데이터 클래스
# ══════════════════════════════════════════════════════════════
@dataclass
class SurgeResult:
    market: str
    score: float
    is_surge: bool
    grade: str

    volume_score: float = 0.0
    taker_score: float = 0.0
    price_score: float = 0.0
    ob_score: float = 0.0
    btc_score: float = 0.0
    accum_score: float = 0.0
    momentum_score: float = 0.0

    vol_ratio: float = 0.0
    obv_trend: float = 0.0
    taker_buy_ratio: float = 0.5
    breakout_pct: float = 0.0
    week52_pct: float = 0.0
    obi: float = 0.0
    ob_pressure: float = 1.0
    bid_wall_level: float = 0.0
    spread_pct: float = 0.0
    btc_decoupling: float = 0.0
    wyckoff_score: float = 0.0
    rsi: float = 50.0
    macd_cross: bool = False
    obv_rising: bool = False
    time_weight: float = 1.0
    mtf_aligned: bool = False
    pump_dump_flag: bool = False
    price_change_1m: float = 0.0
    price_change_5m: float = 0.0
    price_change_15m: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict:
        return {k: round(v, 4) if isinstance(v, float) else v
                for k, v in self.__dict__.items()}


@dataclass
class SurgeConfig:
    """모든 파라미터 - 나중에 튜닝 가능"""
    threshold_s: float = 0.80
    threshold_a: float = 0.65
    threshold_b: float = 0.50
    threshold_c: float = 0.35

    weight_volume: float = 0.20
    weight_taker: float = 0.18
    weight_price: float = 0.18
    weight_ob: float = 0.15
    weight_btc: float = 0.12
    weight_accum: float = 0.10
    weight_momentum: float = 0.07

    vol_period: int = 20
    vol_spike_min: float = 3.0
    vol_spike_strong: float = 7.0
    vol_spike_extreme: float = 15.0

    taker_buy_strong: float = 0.65
    taker_buy_extreme: float = 0.80
    taker_count_min: int = 20

    breakout_period_short: int = 20
    breakout_period_long: int = 60
    breakout_min_pct: float = 0.005

    ob_depth: int = 15
    obi_strong: float = 0.20
    obi_extreme: float = 0.40
    spread_max_pct: float = 0.005

    decoupling_min: float = 0.005
    decoupling_strong: float = 0.02

    accum_vol_increase_min: float = 1.3
    accum_price_flat_max: float = 0.015
    accum_period: int = 10

    rsi_min: float = 50.0
    rsi_strong: float = 60.0
    macd_lookback: int = 3

    time_active_boost: float = 1.20
    time_inactive_penalty: float = 0.85

    pnd_vol_spike_min: float = 5.0
    pnd_bearish_candle: float = -0.005

    mtf_aligned_bonus: float = 0.08


# ══════════════════════════════════════════════════════════════
# 메인 클래스
# ══════════════════════════════════════════════════════════════
class SurgeDetector:
    """
    전문 급등 포착 엔진 v2.1.0
    타임프레임: 1분봉(진입감지) + 5분봉(맥락) + 15분봉(매집)
    """

    def __init__(self, config: Optional[SurgeConfig] = None):
        self.cfg = config or SurgeConfig()

    def analyze(
        self,
        market: str,
        df_1m,                              # 1분봉 OHLCV (80개 이상)
        df_5m=None,                         # 5분봉 OHLCV (MTF + BTC 역행)
        df_15m=None,                        # 15분봉 OHLCV (세력 매집)
        ticks: Optional[List[dict]] = None, # 체결 내역 (ask_bid 포함)
        orderbook: Optional[dict] = None,   # 오더북
        btc_df_5m=None,                     # BTC 5분봉 (역행 분석)
        ticker: Optional[dict] = None,      # 현재가 API (52주 고점)
    ) -> SurgeResult:
        """
        급등 종합 분석

        Args:
            market   : KRW-XXX
            df_1m    : 1분봉 OHLCV (거래량 폭발, 전고점 돌파용)
            df_5m    : 5분봉 OHLCV (BTC 역행, MTF용)
            df_15m   : 15분봉 OHLCV (세력 매집용)
            ticks    : 체결 내역 list
            orderbook: Upbit 오더북 dict
            btc_df_5m: BTC 5분봉 OHLCV
            ticker   : Upbit ticker dict
        """
        try:
            if df_1m is None or len(df_1m) < 20:
                return self._empty(market, "1분봉데이터부족")

            closes_1m = df_1m["close"].astype(float)
            highs_1m  = df_1m["high"].astype(float)
            lows_1m   = df_1m["low"].astype(float)
            vols_1m   = df_1m["volume"].astype(float)

            # ── 개별 신호 계산 ────────────────────────────────
            # 신호1: 1분봉 거래량 폭발
            vol_score, vol_ratio, obv_trend = self._signal_volume(
                closes_1m, vols_1m
            )
            # 신호2: 체결 강도 (실시간 ticks)
            taker_score, taker_ratio = self._signal_taker(ticks)

            # 신호3: 1분봉 전고점 돌파
            price_score, breakout, w52 = self._signal_price(
                closes_1m, highs_1m, ticker
            )
            # 신호4: 오더북 불균형
            ob_score, obi, pressure, wall, spread = self._signal_orderbook(
                orderbook, closes_1m
            )
            # 신호5: 5분봉 BTC 역행
            btc_score, decoupling = self._signal_btc(
                df_5m, btc_df_5m
            )
            # 신호6: 15분봉 세력 매집
            accum_score, wyckoff = self._signal_accumulation(df_15m)

            # 신호7: 5분봉 모멘텀 정렬
            mom_score, rsi, macd_x, obv_r = self._signal_momentum(df_5m)

            # ── 펌프앤덤프 필터 ───────────────────────────────
            pump_dump = self._filter_pump_dump(closes_1m, vols_1m, vol_ratio)

            # ── 가중 합산 ─────────────────────────────────────
            raw = (
                vol_score   * self.cfg.weight_volume   +
                taker_score * self.cfg.weight_taker    +
                price_score * self.cfg.weight_price    +
                ob_score    * self.cfg.weight_ob       +
                btc_score   * self.cfg.weight_btc      +
                accum_score * self.cfg.weight_accum    +
                mom_score   * self.cfg.weight_momentum
            )

            if pump_dump:
                raw *= 0.3

            # ── 시간대 가중치 ─────────────────────────────────
            time_w = self._time_weight()
            raw   *= time_w

            # ── MTF 정렬 보너스 ───────────────────────────────
            mtf_aligned = self._check_mtf(df_5m, df_15m)
            if mtf_aligned and not pump_dump:
                raw += self.cfg.mtf_aligned_bonus

            score = round(min(max(raw, 0.0), 1.0), 4)
            grade = self._grade(score)
            is_surge = score >= self.cfg.threshold_a

            # ── 가격 변화율 (1분/5분/15분) ────────────────────
            pc_1m  = float((closes_1m.iloc[-1] - closes_1m.iloc[-2])  / closes_1m.iloc[-2])  if len(closes_1m) >= 2  else 0.0
            pc_5m  = float((closes_1m.iloc[-1] - closes_1m.iloc[-6])  / closes_1m.iloc[-6])  if len(closes_1m) >= 6  else 0.0
            pc_15m = float((closes_1m.iloc[-1] - closes_1m.iloc[-16]) / closes_1m.iloc[-16]) if len(closes_1m) >= 16 else 0.0

            # ── 진입 근거 요약 ────────────────────────────────
            reasons = []
            if vol_score   >= 0.5: reasons.append(f"거래량{vol_ratio:.1f}x[1m]")
            if taker_score >= 0.5: reasons.append(f"체결강도{taker_ratio:.0%}")
            if price_score >= 0.5: reasons.append(f"전고점돌파{breakout*100:.1f}%[1m]")
            if ob_score    >= 0.5: reasons.append(f"OBI{obi:+.2f}")
            if btc_score   >= 0.5: reasons.append(f"BTC역행{decoupling*100:+.1f}%[5m]")
            if accum_score >= 0.5: reasons.append(f"매집{wyckoff:.2f}[15m]")
            if mom_score   >= 0.5: reasons.append(f"RSI{rsi:.0f}[5m]")
            if mtf_aligned:        reasons.append("MTF정렬[5m+15m]")
            if pump_dump:          reasons.append("PND필터")
            reason = " | ".join(reasons) or "신호미약"

            result = SurgeResult(
                market=market, score=score, is_surge=is_surge, grade=grade,
                volume_score=vol_score, taker_score=taker_score,
                price_score=price_score, ob_score=ob_score,
                btc_score=btc_score, accum_score=accum_score,
                momentum_score=mom_score,
                vol_ratio=vol_ratio, obv_trend=obv_trend,
                taker_buy_ratio=taker_ratio,
                breakout_pct=breakout, week52_pct=w52,
                obi=obi, ob_pressure=pressure,
                bid_wall_level=wall, spread_pct=spread,
                btc_decoupling=decoupling,
                wyckoff_score=wyckoff,
                rsi=rsi, macd_cross=macd_x, obv_rising=obv_r,
                time_weight=time_w, mtf_aligned=mtf_aligned,
                pump_dump_flag=pump_dump,
                price_change_1m=pc_1m,
                price_change_5m=pc_5m,
                price_change_15m=pc_15m,
                reason=reason,
            )

            if is_surge:
                logger.info(
                    f"[SurgeDetector] {market} | {grade}급 | "
                    f"score={score:.3f} | {reason}"
                )
            return result

        except Exception as e:
            logger.debug(f"[SurgeDetector] {market} 오류: {e}")
            return self._empty(market, str(e))

    # ══════════════════════════════════════════════════════════
    # 신호 1: 1분봉 거래량 폭발 + OBV
    # ══════════════════════════════════════════════════════════
    def _signal_volume(self, closes, vols) -> tuple:
        try:
            if len(vols) < self.cfg.vol_period + 1:
                return 0.0, 0.0, 0.0
            recent = float(vols.iloc[-1])
            avg    = float(vols.iloc[-(self.cfg.vol_period+1):-1].mean())
            if avg <= 0:
                return 0.0, 0.0, 0.0
            ratio = recent / avg
            if ratio < self.cfg.vol_spike_min:
                vscore = 0.0
            elif ratio < self.cfg.vol_spike_strong:
                vscore = 0.3 + (ratio - self.cfg.vol_spike_min) / \
                         (self.cfg.vol_spike_strong - self.cfg.vol_spike_min) * 0.4
            elif ratio < self.cfg.vol_spike_extreme:
                vscore = 0.7 + (ratio - self.cfg.vol_spike_strong) / \
                         (self.cfg.vol_spike_extreme - self.cfg.vol_spike_strong) * 0.2
            else:
                vscore = 0.95
            obv = self._calc_obv(closes, vols)
            obv_trend = 0.0
            if obv and len(obv) >= 5:
                obv_slope = (obv[-1] - obv[-6]) / (abs(obv[-6]) + 1e-9)
                obv_trend = obv_slope
                if obv_slope > 0.05:
                    vscore = min(vscore + 0.1, 1.0)
            return round(vscore, 4), round(ratio, 2), round(obv_trend, 4)
        except Exception:
            return 0.0, 0.0, 0.0

    # ══════════════════════════════════════════════════════════
    # 신호 2: 체결 강도 (Taker Buy Ratio)
    # ══════════════════════════════════════════════════════════
    def _signal_taker(self, ticks) -> tuple:
        try:
            if not ticks or len(ticks) < self.cfg.taker_count_min:
                return 0.0, 0.5
            bid_vol = sum(float(t.get("trade_volume", 0))
                          for t in ticks if t.get("ask_bid") == "BID")
            ask_vol = sum(float(t.get("trade_volume", 0))
                          for t in ticks if t.get("ask_bid") == "ASK")
            total = bid_vol + ask_vol
            if total <= 0:
                return 0.0, 0.5
            ratio = bid_vol / total
            if ratio < 0.55:
                score = 0.0
            elif ratio < self.cfg.taker_buy_strong:
                score = 0.3 + (ratio - 0.55) / \
                        (self.cfg.taker_buy_strong - 0.55) * 0.35
            elif ratio < self.cfg.taker_buy_extreme:
                score = 0.65 + (ratio - self.cfg.taker_buy_strong) / \
                        (self.cfg.taker_buy_extreme - self.cfg.taker_buy_strong) * 0.25
            else:
                score = 0.95
            return round(score, 4), round(ratio, 4)
        except Exception:
            return 0.0, 0.5

    # ══════════════════════════════════════════════════════════
    # 신호 3: 1분봉 전고점/52주 고점 돌파
    # ══════════════════════════════════════════════════════════
    def _signal_price(self, closes, highs, ticker) -> tuple:
        try:
            current = float(closes.iloc[-1])
            scores, breakouts = [], []
            for period in [self.cfg.breakout_period_short,
                           self.cfg.breakout_period_long]:
                if len(highs) < period + 1:
                    continue
                past_high = float(highs.iloc[-(period+1):-1].max())
                if past_high <= 0:
                    continue
                pct = (current - past_high) / past_high
                if pct >= self.cfg.breakout_min_pct:
                    scores.append(min(0.4 + pct * 10, 0.95))
                    breakouts.append(pct)
            w52_pct = 0.0
            if ticker:
                high52 = float(ticker.get("highest_52_week_price", 0))
                if high52 > 0:
                    w52_pct = (current - high52) / high52
                    if w52_pct >= 0:
                        scores.append(min(0.7 + w52_pct * 5, 1.0))
            if not scores:
                return 0.0, 0.0, w52_pct
            return (round(max(scores), 4),
                    round(max(breakouts) if breakouts else 0.0, 4),
                    round(w52_pct, 4))
        except Exception:
            return 0.0, 0.0, 0.0

    # ══════════════════════════════════════════════════════════
    # 신호 4: 오더북 불균형 (OBI + 매수벽 + 스프레드)
    # ══════════════════════════════════════════════════════════
    def _signal_orderbook(self, orderbook, closes) -> tuple:
        try:
            if not orderbook:
                return 0.0, 0.0, 1.0, 0.0, 0.0
            units = orderbook.get("orderbook_units", [])
            if not units:
                return 0.0, 0.0, 1.0, 0.0, 0.0
            depth   = min(self.cfg.ob_depth, len(units))
            current = float(closes.iloc[-1]) if len(closes) > 0 else 0
            total_bid = float(orderbook.get("total_bid_size", 0))
            total_ask = float(orderbook.get("total_ask_size", 0))
            obi = (total_bid - total_ask) / (total_bid + total_ask) \
                  if total_bid + total_ask > 0 else 0.0
            wall_level, max_bid_size = 0.0, 0.0
            bid_total, ask_total = 0.0, 0.0
            for u in units[:depth]:
                bp = float(u.get("bid_price", 0))
                bs = float(u.get("bid_size", 0)) * bp
                ap = float(u.get("ask_price", 0))
                as_ = float(u.get("ask_size", 0)) * ap
                bid_total += bs
                ask_total += as_
                pct_below = (current - bp) / current if current > 0 else 0
                if 0.005 <= pct_below <= 0.06 and bs > max_bid_size:
                    max_bid_size = bs
                    wall_level   = pct_below
            pressure = bid_total / ask_total if ask_total > 0 else 1.0
            best_bid = float(units[0].get("bid_price", 0))
            best_ask = float(units[0].get("ask_price", 0))
            spread   = (best_ask - best_bid) / best_ask if best_ask > 0 else 0.0
            if obi < 0.10:
                score = 0.0
            elif obi < self.cfg.obi_strong:
                score = 0.3 + (obi - 0.10) / (self.cfg.obi_strong - 0.10) * 0.3
            elif obi < self.cfg.obi_extreme:
                score = 0.6 + (obi - self.cfg.obi_strong) / \
                        (self.cfg.obi_extreme - self.cfg.obi_strong) * 0.3
            else:
                score = 0.95
            if spread > self.cfg.spread_max_pct:
                score *= 0.7
            if wall_level > 0:
                score = min(score + 0.1, 1.0)
            return (round(score, 4), round(obi, 4),
                    round(pressure, 3), round(wall_level, 4), round(spread, 5))
        except Exception:
            return 0.0, 0.0, 1.0, 0.0, 0.0

    # ══════════════════════════════════════════════════════════
    # 신호 5: 5분봉 BTC 역행 강도
    # ══════════════════════════════════════════════════════════
    def _signal_btc(self, df_5m, btc_df_5m) -> tuple:
        """
        5분봉 기준 BTC 역행 강도
        BTC 하락/횡보 중 코인 독립 상승 → 강한 매수 압력 신호
        """
        try:
            if df_5m is None or len(df_5m) < 2:
                return 0.0, 0.0
            closes = df_5m["close"].astype(float)
            coin_5m = float((closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2])
            coin_15m = float((closes.iloc[-1] - closes.iloc[-4]) / closes.iloc[-4]) \
                       if len(closes) >= 4 else coin_5m
            if btc_df_5m is None or len(btc_df_5m) < 2:
                score = min(max(coin_5m * 15, 0.0), 0.7)
                return round(score, 4), round(coin_5m, 4)
            btc_closes = btc_df_5m["close"].astype(float)
            btc_5m  = float((btc_closes.iloc[-1] - btc_closes.iloc[-2]) / btc_closes.iloc[-2])
            btc_15m = float((btc_closes.iloc[-1] - btc_closes.iloc[-4]) / btc_closes.iloc[-4]) \
                      if len(btc_closes) >= 4 else btc_5m
            decoupling = (coin_5m - btc_5m) * 0.6 + (coin_15m - btc_15m) * 0.4
            if decoupling < self.cfg.decoupling_min:
                score = 0.0
            elif decoupling < self.cfg.decoupling_strong:
                score = 0.4 + (decoupling - self.cfg.decoupling_min) / \
                        (self.cfg.decoupling_strong - self.cfg.decoupling_min) * 0.4
            else:
                score = min(0.8 + (decoupling - self.cfg.decoupling_strong) * 5, 0.95)
            if btc_5m > 0.01:
                score *= 0.5
            return round(score, 4), round(decoupling, 4)
        except Exception:
            return 0.0, 0.0

    # ══════════════════════════════════════════════════════════
    # 신호 6: 15분봉 세력 매집 (Wyckoff)
    # ══════════════════════════════════════════════════════════
    def _signal_accumulation(self, df_15m) -> tuple:
        """
        15분봉 기준 Wyckoff 매집 패턴:
        1. 가격 횡보 (변동성 낮음)
        2. 거래량 점진적 증가
        3. Higher Lows (저점 상승)
        4. OBV 지속 상승
        """
        try:
            if df_15m is None or len(df_15m) < self.cfg.accum_period * 2:
                return 0.0, 0.0
            closes = df_15m["close"].astype(float)
            vols   = df_15m["volume"].astype(float)
            lows   = df_15m["low"].astype(float)
            n = self.cfg.accum_period
            rc = closes.iloc[-n:].values
            pc = closes.iloc[-(n*2):-n].values
            rv = vols.iloc[-n:].values
            pv = vols.iloc[-(n*2):-n].values
            rl = lows.iloc[-n:].values
            scores = []
            price_range = (max(rc) - min(rc)) / (np.mean(rc) + 1e-9)
            if price_range < self.cfg.accum_price_flat_max:
                scores.append(0.8)
            elif price_range < self.cfg.accum_price_flat_max * 2:
                scores.append(0.4)
            else:
                scores.append(0.0)
            prev_avg = np.mean(pv)
            if prev_avg > 0:
                vi = np.mean(rv) / prev_avg
                scores.append(min(0.4 + (vi - 1.3) * 0.5, 0.9) if vi >= 1.3 else 0.0)
            if len(rl) >= 3:
                slope = np.polyfit(range(len(rl)), rl, 1)[0]
                scores.append(0.7 if slope > 0 else 0.0)
            obv = self._calc_obv(closes, vols)
            if obv and len(obv) >= n:
                obv_r = obv[-n:]
                obv_slope = np.polyfit(range(len(obv_r)), obv_r, 1)[0]
                scores.append(0.6 if obv_slope > 0 else 0.0)
            if not scores:
                return 0.0, 0.0
            wyckoff = float(np.mean(scores))
            return round(wyckoff, 4), round(wyckoff, 4)
        except Exception:
            return 0.0, 0.0

    # ══════════════════════════════════════════════════════════
    # 신호 7: 5분봉 모멘텀 정렬 (RSI + MACD + OBV)
    # ══════════════════════════════════════════════════════════
    def _signal_momentum(self, df_5m) -> tuple:
        try:
            if df_5m is None or len(df_5m) < 30:
                return 0.0, 50.0, False, False
            closes = df_5m["close"].astype(float)
            vols   = df_5m["volume"].astype(float)
            rsi = self._calc_rsi(closes)
            if rsi is None:
                return 0.0, 50.0, False, False
            macd, sig = self._calc_macd(closes)
            obv       = self._calc_obv(closes, vols)
            scores    = []
            if rsi >= self.cfg.rsi_strong:
                scores.append(0.8)
            elif rsi >= self.cfg.rsi_min:
                scores.append(0.5)
            else:
                scores.append(0.0)
            macd_cross = False
            if macd is not None and len(macd) >= self.cfg.macd_lookback:
                m = macd[-self.cfg.macd_lookback:]
                s = sig[-self.cfg.macd_lookback:]
                if m[-1] > s[-1]:
                    macd_cross = True
                    scores.append(0.9 if m[0] <= s[0] else 0.6)
                else:
                    scores.append(0.0)
            obv_rising = False
            if obv and len(obv) >= 5:
                obv_rising = obv[-1] > obv[-5]
                scores.append(0.7 if obv_rising else 0.0)
            score = float(np.mean(scores)) if scores else 0.0
            return round(score, 4), round(float(rsi), 2), macd_cross, obv_rising
        except Exception:
            return 0.0, 50.0, False, False

    # ══════════════════════════════════════════════════════════
    # 펌프앤덤프 필터 (1분봉 기준)
    # ══════════════════════════════════════════════════════════
    def _filter_pump_dump(self, closes, vols, vol_ratio: float) -> bool:
        try:
            if len(closes) < 3 or len(vols) < 3:
                return False
            candle_change = float(
                (closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2]
            )
            if (vol_ratio >= self.cfg.pnd_vol_spike_min and
                    candle_change < self.cfg.pnd_bearish_candle):
                return True
            prev_vol    = float(vols.iloc[-2])
            current_vol = float(vols.iloc[-1])
            if (prev_vol > 0 and vol_ratio >= self.cfg.pnd_vol_spike_min and
                    current_vol / prev_vol < 0.2):
                return True
            return False
        except Exception as _e:
            import logging as _lg
            _lg.getLogger("surge_detector").debug(f"[WARN] surge_detector 오류 무시: {_e}")
            return False

    # ══════════════════════════════════════════════════════════
    # 시간대 가중치 (KST)
    # ══════════════════════════════════════════════════════════
    def _time_weight(self) -> float:
        try:
            hour = datetime.now().hour
            if (14 <= hour < 16) or (22 <= hour < 24) or (0 <= hour < 2):
                return self.cfg.time_active_boost
            elif 4 <= hour < 8:
                return self.cfg.time_inactive_penalty
            return 1.0
        except Exception:
            return 1.0

    # ══════════════════════════════════════════════════════════
    # MTF 정렬: 5분봉 + 15분봉 EMA20 동시 확인
    # ══════════════════════════════════════════════════════════
    def _check_mtf(self, df_5m, df_15m) -> bool:
        try:
            if df_5m is None or len(df_5m) < 20:
                return False
            c5  = df_5m["close"].astype(float)
            e5  = c5.ewm(span=20, adjust=False).mean()
            tf5 = float(c5.iloc[-1]) > float(e5.iloc[-1])
            if df_15m is None or len(df_15m) < 20:
                return tf5
            c15  = df_15m["close"].astype(float)
            e15  = c15.ewm(span=20, adjust=False).mean()
            tf15 = float(c15.iloc[-1]) > float(e15.iloc[-1])
            return tf5 and tf15
        except Exception as _e:
            import logging as _lg
            _lg.getLogger("surge_detector").debug(f"[WARN] surge_detector 오류 무시: {_e}")
            return False

    # ══════════════════════════════════════════════════════════
    # 보조 계산
    # ══════════════════════════════════════════════════════════
    def _calc_obv(self, closes, vols):
        try:
            obv = [0.0]
            for i in range(1, len(closes)):
                c = float(closes.iloc[i])
                p = float(closes.iloc[i-1])
                v = float(vols.iloc[i])
                obv.append(obv[-1] + (v if c > p else (-v if c < p else 0)))
            return obv
        except Exception as _e:
            import logging as _lg
            _lg.getLogger("surge_detector").debug(f"[WARN] surge_detector 오류 무시: {_e}")
            return None

    def _calc_rsi(self, closes, period: int = 14) -> Optional[float]:
        try:
            if len(closes) < period + 1:
                return None
            delta = closes.diff().dropna()
            gain  = delta.clip(lower=0).iloc[-period:].mean()
            loss  = (-delta).clip(lower=0).iloc[-period:].mean()
            if loss == 0:
                return 100.0
            return float(100 - 100 / (1 + gain / loss))
        except Exception as _e:
            import logging as _lg
            _lg.getLogger("surge_detector").debug(f"[WARN] surge_detector 오류 무시: {_e}")
            return None

    def _calc_macd(self, closes, fast=12, slow=26, signal=9):
        try:
            if len(closes) < slow + signal:
                return None, None
            ema_f = closes.ewm(span=fast,   adjust=False).mean()
            ema_s = closes.ewm(span=slow,   adjust=False).mean()
            macd  = ema_f - ema_s
            sig   = macd.ewm(span=signal, adjust=False).mean()
            return macd.values, sig.values
        except Exception:
            return None, None

    # ══════════════════════════════════════════════════════════
    # 등급 / 빈 결과
    # ══════════════════════════════════════════════════════════
    def _grade(self, score: float) -> str:
        if score >= self.cfg.threshold_s: return "S"
        if score >= self.cfg.threshold_a: return "A"
        if score >= self.cfg.threshold_b: return "B"
        if score >= self.cfg.threshold_c: return "C"
        return "NONE"

    def _empty(self, market: str, reason: str = "") -> SurgeResult:
        return SurgeResult(
            market=market, score=0.0,
            is_surge=False, grade="NONE", reason=reason
        )