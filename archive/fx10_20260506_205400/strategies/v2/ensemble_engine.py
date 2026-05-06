from __future__ import annotations
import threading
import time
import sqlite3
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from loguru import logger
from core.constants import DISABLED_STRATEGIES as _GLOBAL_DISABLED
from strategies.base_strategy import BaseStrategy, Signal, SignalType
from strategies.v2.context.market_context import MarketContextEngine, MarketContext
from strategies.v2.order_block_v2 import OrderBlockStrategy2
# [FX-1] VolBreakout 비활성화 (-₩3,521) — import 불필요
# from strategies.v2.vol_breakout_v2 import VolBreakoutStrategy2
from strategies.v2.supertrend_v2 import SupertrendStrategy2
# [FX-1] VWAP_Reversion 비활성화 (-₩3,158) — import 불필요
# from strategies.v2.vwap_v2 import VWAPReversionStrategy2
from strategies.v2.macd_v2 import MACDCrossStrategy2
from strategies.v2.rsi_v2 import RSIDivergenceStrategy2
from strategies.v2.bollinger_v2 import BollingerSqueezeStrategy2
from strategies.v2.atr_v2 import ATRChannelStrategy2


@dataclass
class StrategyWeight:
    name: str
    base_weight: float
    recent_wr: float        # 최근 20거래 승률
    dynamic_weight: float   # 최종 동적 가중치
    signal_count: int = 0   # 오늘 신호 수
    win_count: int    = 0   # 오늘 승리 수


@dataclass
class EnsembleDecision:
    should_enter: bool
    final_score: float
    confidence: float
    position_size_mult: float   # 0.5 ~ 1.5
    signals_fired: List[str]
    dominant_strategy: str
    regime: str
    reasoning: str


class EnsembleEngine:
    """
    앙상블 최종 결정 엔진
    8개 전략의 동적 가중치 합산
    최근 20거래 승률로 가중치 자동 조정
    시장 레짐별 전략 우선순위 변경
    """

    # 기본 가중치 (config/optimized_params.json 우선, 없으면 아래 기본값)
    BASE_WEIGHTS: Dict[str, float] = {
        "MACD_Cross":        1.2,
        "RSI_Divergence":    1.7,
        "Bollinger_Squeeze": 1.6,
        "ATR_Channel":       1.5,
        "OrderBlock_SMC":    0.0,  # [FX6c] 비활성화 (6건 전패)
        "Supertrend":        0.8,
        # [ST-1] VWAP_Reversion 비활성화: DB -₩3,158, 42% 승률 (2026-05-03)
        # "VWAP_Reversion":    0.5,
        # [ST-2] VolBreakout 비활성화: DB -₩3,521, 29% 승률 (2026-05-03)
        # "VolBreakout":       0.3,
    }

    # [FIX-B] 비활성화 전략 목록 — 여기서만 관리
    # 추가 시: 전략명을 이 set에 추가하면 앙상블 전체에서 자동 제외
    DISABLED_STRATEGIES: set = _GLOBAL_DISABLED  # [REFACTOR] constants.py 단일 관리

    REFERENCE_WR:     float = 0.55
    MIN_SIGNALS_NEEDED: int  = 1   # [P6-PATCH] 2→1: BEAR_WATCH 레짐 신호 빈도 대응
    ENTRY_THRESHOLD:  float = 0.55

    # 레짐별 전략 부스트 [EN-H2] GlobalRegime 실제 enum 값과 일치하도록 수정
    # GlobalRegime: TRENDING_UP / TRENDING_DOWN / RANGING / VOLATILE
    #               BEAR_REVERSAL / RECOVERY / UNKNOWN
    REGIME_BOOSTS: Dict[str, Dict[str, float]] = {
        # [R4-PATCH] GlobalRegime 실제 enum 값과 완전 일치 — BULL/BEAR_WATCH/BEAR 추가
        "BULL":          {"MACD_Cross": 1.3, "Supertrend": 1.4,
                          "OrderBlock_SMC": 1.2, "ATR_Channel": 1.1},
        "RANGING":       {"Bollinger_Squeeze": 1.2, "RSI_Divergence": 1.1},
        "TRENDING_UP":   {"Supertrend": 1.4, "MACD_Cross": 1.3, "ATR_Channel": 1.2,
                          "OrderBlock_SMC": 1.1},
        "TRENDING_DOWN": {"RSI_Divergence": 1.2, "Supertrend": 0.7},
        "VOLATILE":      {"OrderBlock_SMC": 1.3, "ATR_Channel": 1.2},
        "BEAR_REVERSAL": {"RSI_Divergence": 1.3, "OrderBlock_SMC": 1.2},
        "BEAR_WATCH":    {"Bollinger_Squeeze": 1.3, "MACD_Cross": 1.1,
                          "RSI_Divergence": 1.2},
        "BEAR":          {"Bollinger_Squeeze": 1.4, "RSI_Divergence": 1.3},
        "RECOVERY":      {"MACD_Cross": 1.3, "OrderBlock_SMC": 1.2, "Bollinger_Squeeze": 1.1},
        "UNKNOWN":       {},
    }

    @staticmethod
    def _load_base_weights() -> dict:
        """config/optimized_params.json 에서 전략별 boost 반환 [EN-M1 로그 강화]"""
        try:
            import json as _j, pathlib as _pl
            _cfg = _j.loads(_pl.Path('config/optimized_params.json')
                            .read_text(encoding='utf-8'))
            _strats = _cfg.get('strategies', {})
            _MAP = {
                'Order_Block':       'OrderBlock_SMC',
                'Bollinger_Squeeze': 'Bollinger_Squeeze',
                'RSI_Divergence':    'RSI_Divergence',
                'MACD_Cross':        'MACD_Cross',
                'ATR_Channel':       'ATR_Channel',
                # [FIX-A] 'VWAP_Reversion': 'VWAP_Reversion',  # 비활성화: -₩3,158
                'Supertrend':        'Supertrend',
                # [ST-2] 'Vol_Breakout': 'VolBreakout',  # 비활성화: -₩3,521
            }
            result = {eng_k: _strats[cfg_k].get('boost', 1.0)
                      for cfg_k, eng_k in _MAP.items()
                      if cfg_k in _strats}
            # [EN-M1] 적용값 명시 로그
            for eng_k, boost_v in result.items():
                logger.debug(f'[Ensemble] config boost 적용: {eng_k:20s} boost={boost_v:.3f}')
            return result
        except Exception as _e:
            logger.debug(f'[Ensemble] config 로드 실패: {_e}')
            return {}

    def __init__(self, settings=None):
        # config boost 값 반영
        _cfg_boosts = self._load_base_weights()
        # [FX9-2] BASE_WEIGHTS 초기화 버그 수정
        # config boost를 명시적으로 곱함 (실패 시 원래 기본값 유지)
        _fixed_base = {
            'MACD_Cross':        1.2,
            'RSI_Divergence':    1.7,
            'Bollinger_Squeeze': 1.6,
            'ATR_Channel':       1.5,
            'OrderBlock_SMC':    0.0,
            'Supertrend':        0.8,
        }
        self.BASE_WEIGHTS = {
            k: round(_fixed_base[k] * _cfg_boosts.get(k, 1.0), 3)
            for k in _fixed_base
        }
        # [FIX-C] 비활성화 전략 이중 필터링
        # → _MAP 주석처리(FIX-A)로 이미 차단, 여기서 최종 방어
        for _dis in self.DISABLED_STRATEGIES:
            self.BASE_WEIGHTS.pop(_dis, None)
        if _cfg_boosts:
            logger.info(f'[Ensemble] config boost {len(_cfg_boosts)}개 적용')
        else:
            logger.warning('[Ensemble] config 로드 실패 — 기본 가중치 사용')
        # 필수 속성 초기화
        self._db_path = 'database/apex_bot.db'
        try:
            # [FX-2] 경로 통일: strategies.v2.context.market_context (core.market_context 제거)
            self._context_engine = MarketContextEngine()
        except Exception:
            self._context_engine = None
        self._weights: Dict[str, StrategyWeight] = {}
        self._strategies: Dict[str, BaseStrategy] = {}
        self._init_strategies()
        self._load_recent_performance()


    def _init_strategies(self):
        self._strategies = {
            "MACD_Cross":        MACDCrossStrategy2(),
            "RSI_Divergence":    RSIDivergenceStrategy2(),
            "Bollinger_Squeeze": BollingerSqueezeStrategy2(),
            "ATR_Channel":       ATRChannelStrategy2(),
            # [FX7-1] OrderBlock_SMC 완전 비활성화 (weight=0.0, 6건 전패)
            # "OrderBlock_SMC":    OrderBlockStrategy2(),
            "Supertrend":        SupertrendStrategy2(),
            # [ST-1] VWAP_Reversion 비활성화: 손실 전략 (-₩3,158, 42% 승률)
            # "VWAP_Reversion":    VWAPReversionStrategy2(),
            # [ST-2] VolBreakout 비활성화: 손실 전략 (-₩3,521, 29% 승률)
            # "VolBreakout":       VolBreakoutStrategy2(),
        }
        for name, base_w in self.BASE_WEIGHTS.items():
            self._weights[name] = StrategyWeight(
                name=name,
                base_weight=base_w,
                recent_wr=self.REFERENCE_WR,
                dynamic_weight=base_w,
            )
        logger.info(f"[Ensemble] {len(self._strategies)}개 전략 초기화 완료 (VWAP/VolBreakout 비활성화)")

    def _load_recent_performance(self):
        """DB에서 최근 20거래 승률 로드 → 동적 가중치 계산
        [EN-Q1]  try/finally로 연결 누수 방지
        [EN-C3-b] bot_state에서 인메모리 카운터 복원
        """
        conn = None
        try:
            # [EN-Q1] timeout=5: aiosqlite WAL 동시 쓰기 충돌 방어
            conn = sqlite3.connect(self._db_path, timeout=5)
            # [FIX-D] BASE_WEIGHTS → _weights 기준 순회
            # → 비활성화 전략이 BASE_WEIGHTS에 잔류해도 KeyError 방지
            for name in self._weights:
                rows = conn.execute(
                    """
                    SELECT profit_rate FROM trade_history
                    WHERE strategy = ? AND side = 'SELL'
                    ORDER BY timestamp DESC LIMIT 20
                    """,
                    (name,),
                ).fetchall()

                if len(rows) >= 5:
                    # [G2_SharpeWeight] Sharpe 기반 동적 가중치
                    wins      = sum(1 for r in rows if r[0] > 0)
                    wr        = wins / len(rows)
                    _rates    = [r[0] for r in rows]
                    _mean_r   = sum(_rates) / len(_rates)
                    _std_r    = (
                        (sum((x - _mean_r)**2 for x in _rates) / len(_rates)) ** 0.5
                    )
                    # Sharpe = mean / std * sqrt(252); 거래 기반 연환산
                    # 최소 std 방어: 0 나누기 방지
                    _sharpe   = (_mean_r / (_std_r + 1e-9)) * (252 ** 0.5) if _std_r > 1e-6 else 1.0
                    _ref_sharpe = 1.0   # 기준 Sharpe (무조건 1.0으로 정규화)
                    _sharpe_mult = min(2.0, max(0.3, _sharpe / (_ref_sharpe + 1e-9)))
                    # WR 배수 × Sharpe 배수 → 최종 dynamic_weight
                    perf_mult = (wr / self.REFERENCE_WR) * _sharpe_mult
                    new_w     = self._weights[name].base_weight * perf_mult
                    # 클램핑: base × 0.4 ~ base × 2.5
                    new_w     = max(self._weights[name].base_weight * 0.4,
                                   min(new_w, self._weights[name].base_weight * 2.5))
                    self._weights[name].recent_wr      = wr
                    self._weights[name].dynamic_weight = round(new_w, 3)
                    logger.info(
                        f"[Ensemble] {name:20s} WR={wr:.1%} "
                        f"Sharpe={_sharpe:.2f}(×{_sharpe_mult:.2f}) "
                        f"→ weight={new_w:.2f}"
                    )

            # [EN-C3-b] bot_state에서 인메모리 카운터 복원
            # → 재시작 시 update_result() 누적값 유지
            import json as _js_r
            _restored = 0
            # [FIX-E] BASE_WEIGHTS → _weights 기준 순회 (비활성화 전략 제외)
            for name in self._weights:
                try:
                    _key = f"ensemble_counter_{name}"
                    _row = conn.execute(
                        "SELECT value FROM bot_state WHERE key=?",
                        (_key,)
                    ).fetchone()
                    if _row:
                        _data = _js_r.loads(_row[0])
                        self._weights[name].signal_count = _data.get("signal_count", 0)
                        self._weights[name].win_count    = _data.get("win_count", 0)
                        _restored += 1
                        logger.debug(
                            f"[Ensemble] {name} 카운터 복원 | "
                            f"signal={self._weights[name].signal_count} "
                            f"win={self._weights[name].win_count}"
                        )
                except Exception as _ce:
                    logger.debug(f"[Ensemble] {name} 카운터 복원 실패: {_ce}")
            if _restored:
                logger.info(f"[Ensemble] 인메모리 카운터 복원: {_restored}개")

        except Exception as e:
            logger.warning(f"[Ensemble] 성과 로드 실패: {e}")
        finally:
            # [EN-Q1] 예외 발생 시에도 반드시 연결 해제
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def decide(
        self,
        df: pd.DataFrame,
        market: str,
        ctx: Optional[MarketContext] = None,
        fallback_regime: str = 'RANGING',  # [EN-M3] engine_buy에서 GlobalRegime 주입
    ) -> EnsembleDecision:
        """메인 진입 결정 함수"""
        try:
            if ctx is None:
                ctx = (self._context_engine.analyze(df, market)
                   if self._context_engine is not None else None)

            # [EN-M3] ctx 여전히 None이면 fallback_regime 사용
            _regime_str = (
                getattr(ctx, 'regime', fallback_regime)
                if ctx is not None else fallback_regime
            )
            # enum → str 변환 보호
            if hasattr(_regime_str, 'value'):
                _regime_str = _regime_str.value

            signals: Dict[str, Signal] = {}

            # 각 전략 신호 수집
            for name, strategy in self._strategies.items():
                try:
                    sig = strategy.generate_signal(df, market)
                    if sig is not None and sig.signal == SignalType.BUY:
                        signals[name] = sig
                except Exception as e:
                    logger.warning(f"[Ensemble] {name} 신호 오류: {e}")

            if len(signals) < self.MIN_SIGNALS_NEEDED:
                return EnsembleDecision(
                    should_enter=False,
                    final_score=0.0,
                    confidence=0.0,
                    position_size_mult=1.0,
                    signals_fired=[],
                    dominant_strategy="",
                    regime=getattr(ctx, 'regime', 'RANGING'),
                    reasoning=f"신호 부족 ({len(signals)}/{self.MIN_SIGNALS_NEEDED})",
                )

            # 동적 가중치 합산
            regime_boosts = self.REGIME_BOOSTS.get(_regime_str, {})
            total_score   = 0.0
            total_weight  = 0.0
            best_name     = ""
            best_score    = 0.0

            for name, sig in signals.items():
                w     = self._weights[name].dynamic_weight
                boost = regime_boosts.get(name, 1.0)  # [U1-PATCH] 0.0→1.0 기본배율
                final_w = w * boost  # [U1-PATCH] 덧셈→곱셈: 레짐부스트를 배율로 적용
                score   = (sig.score * 0.4 + sig.confidence * 0.6) * final_w
                total_score  += score
                total_weight += final_w
                if score > best_score:
                    best_score = score
                    best_name  = name

            normalized = total_score / total_weight if total_weight > 0 else 0.0

            # 포지션 사이즈 결정
            if normalized >= 0.75:
                size_mult = 1.5
            elif normalized >= 0.65:
                size_mult = 1.2
            elif normalized >= 0.55:
                size_mult = 1.0
            else:
                size_mult = 0.5

            should_enter = normalized >= self.ENTRY_THRESHOLD

            reasoning = (
                f"레짐={_regime_str} | "
                f"신호={len(signals)}개 | "
                f"점수={normalized:.3f} | "
                f"주도전략={best_name}"
            )

            if should_enter:
                logger.info(
                    f"[Ensemble] ✅ {market} 진입결정 | {reasoning} | "
                    f"사이즈배수={size_mult:.1f}"
                )
            else:
                logger.debug(
                    f"[Ensemble] ❌ {market} 진입거부 | {reasoning}"
                )

            # [FP4-PATCH] confidence = 실제 신호 평균 confidence (≠ normalized score)
            _avg_conf = (
                sum(sig.confidence for sig in signals.values()) / len(signals)
                if signals else normalized
            )
            return EnsembleDecision(
                should_enter=should_enter,
                final_score=normalized,
                confidence=_avg_conf,
                position_size_mult=size_mult,
                signals_fired=list(signals.keys()),
                dominant_strategy=best_name,
                regime=_regime_str,
                reasoning=reasoning,
            )

        except Exception as e:
            logger.warning(f"[Ensemble] {market} 결정 오류: {e}")
            return EnsembleDecision(
                should_enter=False,
                final_score=0.0,
                confidence=0.0,
                position_size_mult=1.0,
                signals_fired=[],
                dominant_strategy="",
                regime="UNKNOWN",
                reasoning=f"오류: {e}",
            )

    def update_result(self, strategy_name: str, profit_rate: float):
        """
        거래 결과 반영 → 동적 가중치 실시간 업데이트 [EN-M2]
        - 임계 5→3건으로 완화 (재시작 빈번한 환경 대응)
        - DB 최신 성과와 인메모리 성과 가중 평균으로 안정화
        - 가중치 클램핑: base_weight × 0.5 ~ base_weight × 2.0
        """
        if strategy_name not in self._weights:
            return
        w = self._weights[strategy_name]
        w.signal_count += 1
        if profit_rate > 0:
            w.win_count += 1

        # [EN-M2] 임계 3건으로 완화
        # [EN-C3-a] 카운터를 bot_state 테이블에 영속화
        # → 재시작 시 _load_recent_performance()에서 복원
        # timeout=5: aiosqlite WAL 동시 쓰기 충돌 방어
        # [R5-PATCH] try/finally — SQLite 연결 누수 방지
        _conn_u = None
        try:
            import json as _js_u
            _key = f"ensemble_counter_{strategy_name}"
            _val = _js_u.dumps({
                "signal_count": w.signal_count,
                "win_count":    w.win_count,
            })
            _conn_u = sqlite3.connect(self._db_path, timeout=5)
            _conn_u.execute(
                """
                INSERT INTO bot_state(key, value, updated_at)
                VALUES(?, ?, datetime('now','localtime'))
                ON CONFLICT(key) DO UPDATE
                SET value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (_key, _val)
            )
            _conn_u.commit()
        except Exception as _ue:
            logger.debug(f"[Ensemble] 카운터 저장 실패: {_ue}")
        finally:
            if _conn_u is not None:
                try:
                    _conn_u.close()
                except Exception:
                    pass

        if w.signal_count >= 3:
            mem_wr     = w.win_count / w.signal_count
            blended_wr = w.recent_wr * 0.7 + mem_wr * 0.3
            # [G2_SharpeUpdateResult] update_result에서도 Sharpe 반영
            # DB에서 최근 수익률 조회하여 Sharpe 계산
            try:
                import sqlite3 as _sq2
                _conn2 = sqlite3.connect(self._db_path, timeout=3)
                _rows2 = _conn2.execute(
                    "SELECT profit_rate FROM trade_history "
                    "WHERE strategy=? AND side='SELL' "
                    "ORDER BY timestamp DESC LIMIT 30",
                    (strategy_name,)
                ).fetchall()
                _conn2.close()
                if len(_rows2) >= 5:
                    _rt2    = [r[0] for r in _rows2]
                    _m2     = sum(_rt2) / len(_rt2)
                    _s2     = (sum((x-_m2)**2 for x in _rt2)/len(_rt2))**0.5
                    _sh2    = (_m2 / (_s2 + 1e-9)) * (252**0.5) if _s2 > 1e-6 else 1.0
                    _sm2    = min(2.0, max(0.3, _sh2))
                else:
                    _sm2 = 1.0
            except Exception:
                _sm2 = 1.0
            perf_mult  = (blended_wr / self.REFERENCE_WR) * _sm2
            new_w      = w.base_weight * perf_mult
            # 클램핑: base × 0.4 ~ base × 2.5
            clamped_w  = round(
                max(w.base_weight * 0.4, min(new_w, w.base_weight * 2.5)), 3
            )
            w.recent_wr      = blended_wr
            w.dynamic_weight = clamped_w
            logger.info(
                f"[Ensemble] 가중치 업데이트 | {strategy_name} | "
                f"DB_WR={w.recent_wr:.1%} MEM_WR={mem_wr:.1%} "
                f"blended={blended_wr:.1%} Sharpe×={_sm2:.2f} "
                f"→ weight={clamped_w:.2f}"
            )

    def get_weight_summary(self) -> str:
        lines = ["=== Ensemble 가중치 현황 ==="]
        for name, w in self._weights.items():
            lines.append(
                f"{name:20s} base={w.base_weight:.1f} "
                f"WR={w.recent_wr:.1%} "
                f"dynamic={w.dynamic_weight:.2f}"
            )
        return "\n".join(lines)
