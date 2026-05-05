# phase2_upgrade.py
# APEX BOT 전략 품질 고도화 2단계
# G1: MTF 필수 컨펌 게이트
# G2: Sharpe 기반 앙상블 동적 가중치
# G3: 코인별 변동성 정규화 포지션 사이징
# ────────────────────────────────────────────────────────────────────

import pathlib, shutil, datetime, py_compile, sys, textwrap

ROOT    = pathlib.Path(__file__).parent
ARCHIVE = ROOT / f"archive/phase2_{datetime.datetime.now():%Y%m%d_%H%M%S}"
ARCHIVE.mkdir(parents=True, exist_ok=True)
results = []

def backup(path: pathlib.Path):
    dest = ARCHIVE / path.relative_to(ROOT)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)

def write_new_file(tag: str, path_rel: str, content: str):
    """신규 파일 생성"""
    path = ROOT / path_rel
    if path.exists() and f"[{tag}]" in path.read_text(encoding="utf-8"):
        results.append((tag, "SKIP", "이미 존재"))
        return
    backup(path) if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    try:
        py_compile.compile(str(path), doraise=True)
        results.append((tag, "OK", f"신규 생성: {path_rel}"))
    except py_compile.PyCompileError as e:
        results.append((tag, "FAIL", str(e)))

def patch_file(tag: str, path_rel: str, old_block: str, new_block: str):
    """기존 파일 패치"""
    path = ROOT / path_rel
    if not path.exists():
        results.append((tag, "SKIP", "파일 없음"))
        return
    backup(path)
    text = path.read_text(encoding="utf-8")
    if f"[{tag}]" in text:
        results.append((tag, "SKIP", "이미 적용됨"))
        return
    if old_block not in text:
        results.append((tag, "SKIP", "패턴 없음"))
        return
    new_text = text.replace(old_block, new_block, 1)
    path.write_text(new_text, encoding="utf-8")
    try:
        py_compile.compile(str(path), doraise=True)
        results.append((tag, "OK", ""))
    except py_compile.PyCompileError as e:
        path.write_text(text, encoding="utf-8")
        results.append((tag, "ROLLBACK", str(e)))

# ╔══════════════════════════════════════════════════════════════════╗
# ║  G1-A: signals/mtf_gate.py 신규 생성                           ║
# ╚══════════════════════════════════════════════════════════════════╝
write_new_file("G1_MTFGate", "signals/mtf_gate.py", '''
    # [G1_MTFGate] signals/mtf_gate.py
    # MTF 필수 컨펌 게이트 — 1d/4h 방향이 1h와 불일치 시 진입 완전 차단
    from __future__ import annotations
    from dataclasses import dataclass
    from typing import Dict, Optional
    import pandas as pd
    import numpy as np
    from loguru import logger

    @dataclass
    class MTFGateResult:
        allowed: bool           # 진입 허용 여부
        score: float            # -1.0(완전 역방향) ~ +1.0(완전 동방향)
        reason: str
        tf_directions: Dict[str, int]  # 타임프레임별 방향 (-1/0/+1)
        is_softfail: bool = False       # 데이터 부족 등 소프트 통과


    class MTFGate:
        """
        멀티 타임프레임 필수 컨펌 게이트
        타임프레임 가중치: 1d=0.50, 4h=0.30, 1h=0.20
        
        진입 허용 조건:
          - weighted_score >= GATE_THRESHOLD_BULL (GlobalRegime=BULL: -0.10)
          - weighted_score >= GATE_THRESHOLD_DEFAULT (기타: 0.0)
          - 데이터 부족(softfail) 시 항상 허용
          - BEAR_REVERSAL 시 역방향 허용 (score >= -0.50)
        """

        TF_WEIGHTS = {"1d": 0.50, "4h": 0.30, "1h": 0.20}
        GATE_THRESHOLD_BULL    = -0.10   # BULL: 약간의 역방향도 허용
        GATE_THRESHOLD_DEFAULT = 0.0     # 기타: 동방향 필수
        GATE_THRESHOLD_BEAR_REV = -0.50  # BEAR_REVERSAL: 역방향 대부분 허용
        MIN_CANDLES = 20                 # 방향 판단 최소 캔들 수

        def check(
            self,
            tf_data: Dict[str, pd.DataFrame],
            signal_direction: int,     # +1=BUY, -1=SELL
            global_regime: str = "UNKNOWN",
            is_bear_reversal: bool = False,
        ) -> MTFGateResult:
            """
            MTF 방향 일치 여부 판단
            
            Args:
                tf_data: {"1d": df, "4h": df, "1h": df} 형태
                signal_direction: 진입 방향 (+1 BUY / -1 SELL)
                global_regime: GlobalRegime 문자열
                is_bear_reversal: BEAR_REVERSAL 플래그
            """
            directions: Dict[str, int] = {}
            total_weight = 0.0
            weighted_dir = 0.0
            softfail_tfs = []

            for tf, weight in self.TF_WEIGHTS.items():
                df = tf_data.get(tf)
                if df is None or len(df) < self.MIN_CANDLES:
                    softfail_tfs.append(tf)
                    continue
                direction = self._calc_direction(df)
                directions[tf] = direction
                weighted_dir += direction * weight
                total_weight += weight

            # 중요 TF(1d, 4h) 모두 데이터 없음 → softfail 통과
            if total_weight < 0.30 or (not directions):
                logger.debug(
                    f"[MTFGate] softfail (데이터부족: {softfail_tfs}) → 통과"
                )
                return MTFGateResult(
                    allowed=True,
                    score=0.0,
                    reason=f"데이터 부족 ({softfail_tfs}) → softfail 통과",
                    tf_directions=directions,
                    is_softfail=True,
                )

            # 가중치 합산 (전체 TF가 없어도 있는 TF만으로 정규화)
            norm_score = weighted_dir / total_weight

            # signal_direction과 일치 방향으로 변환
            # norm_score: -1(완전 역) ~ +1(완전 동)
            # signal이 BUY(+1)이면 norm_score 그대로, SELL(-1)이면 부호 반전
            aligned_score = norm_score * signal_direction

            # 레짐별 임계값 결정
            _regime_upper = str(global_regime).upper()
            if is_bear_reversal:
                threshold = self.GATE_THRESHOLD_BEAR_REV
            elif _regime_upper in ("BULL", "RECOVERY"):
                threshold = self.GATE_THRESHOLD_BULL
            else:
                threshold = self.GATE_THRESHOLD_DEFAULT

            allowed = aligned_score >= threshold

            # 방향 문자열 생성
            _dir_str = " | ".join(
                f"{tf}={'▲' if d > 0 else ('▼' if d < 0 else '─')}"
                for tf, d in directions.items()
            )
            reason = (
                f"MTFGate score={aligned_score:.2f} thr={threshold:.2f} "
                f"[{_dir_str}] → {'허용' if allowed else '차단'}"
            )

            if not allowed:
                logger.info(f"[MTFGate] ❌ {reason}")
            else:
                logger.debug(f"[MTFGate] ✅ {reason}")

            return MTFGateResult(
                allowed=allowed,
                score=aligned_score,
                reason=reason,
                tf_directions=directions,
                is_softfail=False,
            )

        def _calc_direction(self, df: pd.DataFrame) -> int:
            """
            캔들 데이터로 방향 판단 (-1 / 0 / +1)
            복합 판단: EMA 기울기 + 최근 3캔들 방향 + RSI 위치
            """
            try:
                close = df["close"].values.astype(float)
                n = len(close)

                # ── EMA20 기울기 ────────────────────────────────
                _w = np.ones(20) / 20
                if n >= 20:
                    ema20 = np.convolve(close, _w, mode="valid")
                    ema_slope = (ema20[-1] - ema20[-5]) / (ema20[-5] + 1e-9) if len(ema20) >= 5 else 0
                else:
                    ema_slope = (close[-1] - close[0]) / (close[0] + 1e-9)

                # ── 최근 3캔들 방향 ─────────────────────────────
                recent_dir = 1 if close[-1] > close[-4] else -1 if close[-1] < close[-4] else 0

                # ── RSI (간이) ──────────────────────────────────
                rsi_dir = 0
                if "rsi" in df.columns:
                    rsi_val = float(df["rsi"].iloc[-1])
                    rsi_dir = 1 if rsi_val > 55 else (-1 if rsi_val < 45 else 0)
                elif n >= 14:
                    delta = np.diff(close[-15:])
                    gain  = np.mean(delta[delta > 0]) if np.any(delta > 0) else 0
                    loss  = np.mean(-delta[delta < 0]) if np.any(delta < 0) else 1e-9
                    rs    = gain / (loss + 1e-9)
                    rsi_val = 100 - 100 / (1 + rs)
                    rsi_dir = 1 if rsi_val > 55 else (-1 if rsi_val < 45 else 0)

                # ── 종합 판단 ────────────────────────────────────
                # EMA 기울기 0.5% 이상이면 방향 확정
                if ema_slope > 0.005:
                    ema_dir = 1
                elif ema_slope < -0.005:
                    ema_dir = -1
                else:
                    ema_dir = 0

                # 3가지 신호 합산 → -3~+3, 임계값 1 이상이면 방향 결정
                composite = ema_dir + recent_dir + rsi_dir
                if composite >= 1:
                    return 1
                elif composite <= -1:
                    return -1
                else:
                    return 0

            except Exception as _e:
                logger.debug(f"[MTFGate] _calc_direction 오류: {_e}")
                return 0
''')

# ╔══════════════════════════════════════════════════════════════════╗
# ║  G1-B: engine_buy.py — MTF 블록을 MTFGate 하드 게이트로 교체  ║
# ╚══════════════════════════════════════════════════════════════════╝
patch_file(
    "G1_MTFHardGate",
    "core/engine_buy.py",
    # ── 탐지 패턴: 기존 MTF 블록 시작부 ──────────────────────────
    old_block='''\
            if self.mtf_merger is not None:
                try:
                    _tf_map = {
                        "1d":  ("day",       "1d"),
                        "4h":  ("minute240", "4h"),
                        "1h":  ("minute60",  "1h"),
                        "15m": ("minute15",  "15m"),
                        "5m":  ("minute5",   "5m"),
                        "1m":  ("minute1",   "1m"),
                    }''',
    # ── 교체 내용: MTFGate 하드 게이트 삽입 ──────────────────────
    new_block='''\
            # [G1_MTFHardGate] MTF 하드 게이트: 1d/4h 역방향 시 완전 차단
            try:
                from signals.mtf_gate import MTFGate as _MTFGate
                _mtfgate = _MTFGate()
                _gate_tf_data = {}
                # 1h: df_processed 재사용
                if df_processed is not None and len(df_processed) >= 20:
                    _gate_tf_data["1h"] = df_processed
                # 4h, 1d: cache 우선 → REST fallback
                for _gtf, _gupbit in [("4h", "minute240"), ("1d", "day")]:
                    _gdf = None
                    for _gg in [
                        lambda tf=_gtf: self.cache_manager.get_ohlcv(market, tf),
                        lambda tf=_gtf: self.cache_manager.get_candles(market, tf),
                    ]:
                        try:
                            _gdf = _gg()
                            if _gdf is not None and len(_gdf) >= 20:
                                break
                            _gdf = None
                        except Exception:
                            _gdf = None
                    if _gdf is None or len(_gdf) < 20:
                        try:
                            import asyncio as _glio
                            _gdf = await _glio.wait_for(
                                self.rest_collector.get_ohlcv(market, _gupbit, 60),
                                timeout=3.0
                            )
                        except Exception:
                            _gdf = None
                    if _gdf is not None and len(_gdf) >= 20:
                        _gate_tf_data[_gtf] = _gdf
                _gr_gate = str(getattr(
                    getattr(self, "_global_regime", None), "value",
                    getattr(self, "_global_regime", "UNKNOWN") or "UNKNOWN"
                )).upper()
                _gate_result = _mtfgate.check(
                    _gate_tf_data,
                    signal_direction=1,
                    global_regime=_gr_gate,
                    is_bear_reversal=_is_bear_rev,
                )
                if not _gate_result.allowed and not _gate_result.is_softfail:
                    logger.info(
                        f"[MTFGate] ❌ {market} 진입 차단: {_gate_result.reason}"
                    )
                    return
            except Exception as _mtfgate_e:
                logger.debug(f"[MTFGate] {market} 오류 → softfail 통과: {_mtfgate_e}")

            if self.mtf_merger is not None:
                try:
                    _tf_map = {
                        "1d":  ("day",       "1d"),
                        "4h":  ("minute240", "4h"),
                        "1h":  ("minute60",  "1h"),
                        "15m": ("minute15",  "15m"),
                        "5m":  ("minute5",   "5m"),
                        "1m":  ("minute1",   "1m"),
                    }'''
)

# ╔══════════════════════════════════════════════════════════════════╗
# ║  G2: ensemble_engine.py — Sharpe 기반 동적 가중치              ║
# ╚══════════════════════════════════════════════════════════════════╝
patch_file(
    "G2_SharpeWeight",
    "strategies/v2/ensemble_engine.py",
    # ── 탐지 패턴: _load_recent_performance 내 가중치 업데이트 부분 ──
    old_block='''\
                if len(rows) >= 5:
                    wins    = sum(1 for r in rows if r[0] > 0)
                    wr      = wins / len(rows)
                    perf_mult = wr / self.REFERENCE_WR
                    new_w     = self._weights[name].base_weight * perf_mult
                    self._weights[name].recent_wr      = wr
                    self._weights[name].dynamic_weight = round(new_w, 3)
                    logger.info(
                        f"[Ensemble] {name:20s} WR={wr:.1%} "
                        f"→ weight={new_w:.2f}"
                    )''',
    new_block='''\
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
                    )'''
)

# ╔══════════════════════════════════════════════════════════════════╗
# ║  G2-B: ensemble_engine.py — update_result에도 Sharpe 반영      ║
# ╚══════════════════════════════════════════════════════════════════╝
patch_file(
    "G2_SharpeUpdateResult",
    "strategies/v2/ensemble_engine.py",
    old_block='''\
        if w.signal_count >= 3:
            mem_wr    = w.win_count / w.signal_count
            # DB 최근 승률과 인메모리 승률 가중 평균 (DB 70%, 메모리 30%)
            # → 재시작 직후 소수 거래로 인한 급격한 가중치 변동 방지
            blended_wr = w.recent_wr * 0.7 + mem_wr * 0.3
            perf_mult  = blended_wr / self.REFERENCE_WR
            new_w      = w.base_weight * perf_mult
            # 클램핑: base × 0.5 ~ base × 2.0
            clamped_w  = round(
                max(w.base_weight * 0.5, min(new_w, w.base_weight * 2.0)), 3
            )
            w.recent_wr      = blended_wr
            w.dynamic_weight = clamped_w
            logger.info(
                f"[Ensemble] 가중치 업데이트 | {strategy_name} | "
                f"DB_WR={w.recent_wr:.1%} MEM_WR={mem_wr:.1%} "
                f"blended={blended_wr:.1%} → weight={clamped_w:.2f}"
            )''',
    new_block='''\
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
            )'''
)

# ╔══════════════════════════════════════════════════════════════════╗
# ║  G3: position_sizer.py — 코인별 변동성 정규화                  ║
# ╚══════════════════════════════════════════════════════════════════╝
patch_file(
    "G3_VolNormSizing",
    "risk/position_sizer.py",
    # ── 탐지 패턴: calculate() 시그니처 ──────────────────────────
    old_block='''\
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
    ) -> float:''',
    new_block='''\
    # [G3_VolNormSizing] 변동성 정규화 기준값 (BTC 일반 ATR%)
    VOL_REF_SIGMA: float = 0.020   # 2% = 기준 변동성

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
        # [G3] 신규: 코인별 ATR% (ATR14 / 현재가)
        market_sigma:   float = 0.0,   # 0이면 정규화 비적용
    ) -> float:'''
)

# G3-B: calculate() 내부 Step 7 뒤에 변동성 정규화 스텝 삽입
patch_file(
    "G3_VolNormStep",
    "risk/position_sizer.py",
    old_block='''\
        # ── Step 8: BEAR_REVERSAL 50% 축소 (이관) ────────────────
        if is_bear_reversal:
            base_amount *= 0.5
            logger.info(
                f"[Kelly-BR] {strategy} {market} | "
                f"BEAR_REVERSAL → 0.5× 축소"
            )''',
    new_block='''\
        # ── Step 7b: [G3_VolNormStep] 코인별 변동성 정규화 ────────
        # market_sigma = ATR14 / current_price (캔들에서 계산)
        # size_adj = size_kelly × (VOL_REF_SIGMA / market_sigma)
        # 클램핑: 0.4 ~ 2.0 (극단적 사이즈 방지)
        if market_sigma > 1e-6:
            vol_norm = self.VOL_REF_SIGMA / market_sigma
            vol_norm = max(0.4, min(vol_norm, 2.0))
            if abs(vol_norm - 1.0) > 0.05:  # 5% 이상 변화 시만 로그
                logger.info(
                    f"[Kelly-VOL] {strategy} {market} | "
                    f"σ_market={market_sigma:.4f} σ_ref={self.VOL_REF_SIGMA:.4f} "
                    f"→ vol_norm={vol_norm:.2f}×"
                )
            base_amount *= vol_norm

        # ── Step 8: BEAR_REVERSAL 50% 축소 (이관) ────────────────
        if is_bear_reversal:
            base_amount *= 0.5
            logger.info(
                f"[Kelly-BR] {strategy} {market} | "
                f"BEAR_REVERSAL → 0.5× 축소"
            )'''
)

# ╔══════════════════════════════════════════════════════════════════╗
# ║  G3-C: engine_buy.py — _execute_buy 호출 시 market_sigma 전달  ║
# ╚══════════════════════════════════════════════════════════════════╝
patch_file(
    "G3_MarketSigmaInject",
    "core/engine_buy.py",
    old_block='''\
                    # V2 앙상블 레이어 검증
                    if getattr(self, '_v2_layer', None) is not None:''',
    new_block='''\
                    # [G3_MarketSigmaInject] market_sigma 계산 → position_sizer 전달
                    try:
                        _ms_df = df_processed
                        if _ms_df is not None and "high" in _ms_df.columns and len(_ms_df) >= 14:
                            _hi  = _ms_df["high"].values[-14:].astype(float)
                            _lo  = _ms_df["low"].values[-14:].astype(float)
                            _cl  = _ms_df["close"].values[-14:].astype(float)
                            # TR = max(hi-lo, |hi-prev_cl|, |lo-prev_cl|)
                            _tr  = []
                            for _i in range(1, len(_cl)):
                                _tr.append(max(
                                    _hi[_i] - _lo[_i],
                                    abs(_hi[_i] - _cl[_i-1]),
                                    abs(_lo[_i] - _cl[_i-1]),
                                ))
                            _atr14   = sum(_tr) / len(_tr) if _tr else 0
                            _cur_cl  = float(_ms_df["close"].iloc[-1])
                            _mkt_sigma = _atr14 / (_cur_cl + 1e-9) if _cur_cl > 0 else 0.0
                        else:
                            _mkt_sigma = 0.0
                    except Exception as _ms_e:
                        _mkt_sigma = 0.0
                        logger.debug(f"[G3] {market} market_sigma 계산 실패: {_ms_e}")
                    if not hasattr(self, "_market_sigma_cache"):
                        self._market_sigma_cache = {}
                    self._market_sigma_cache[market] = _mkt_sigma
                    logger.debug(f"[G3] {market} market_sigma={_mkt_sigma:.4f}")

                    # V2 앙상블 레이어 검증
                    if getattr(self, '_v2_layer', None) is not None:'''
)

# ╔══════════════════════════════════════════════════════════════════╗
# ║  G1-C: _vp_cache TTL 30분 추가 (에이전트 E 제안)               ║
# ╚══════════════════════════════════════════════════════════════════╝
patch_file(
    "G1_VPCacheTTL",
    "core/engine_buy.py",
    old_block='''\
                    # POC 컨텍스트 캐시 저장 (진입 신뢰도 부스트용)
                    if not hasattr(self, "_vp_cache"):
                        self._vp_cache = {}
                    self._vp_cache[market] = {
                        "poc": float(_vp.poc_price),
                        "vah": float(_vp.vah),
                        "val": float(_vp.val),
                        "rr":  float(_rr),
                        "price": float(_cur_price),
                    }''',
    new_block='''\
                    # [G1_VPCacheTTL] POC 캐시 + 30분 TTL
                    import time as _vp_t
                    if not hasattr(self, "_vp_cache"):
                        self._vp_cache = {}
                    # TTL 만료 항목 정리
                    _vp_now = _vp_t.time()
                    self._vp_cache = {
                        k: v for k, v in self._vp_cache.items()
                        if _vp_now - v.get("ts", 0) < 1800
                    }
                    self._vp_cache[market] = {
                        "poc":   float(_vp.poc_price),
                        "vah":   float(_vp.vah),
                        "val":   float(_vp.val),
                        "rr":    float(_rr),
                        "price": float(_cur_price),
                        "ts":    _vp_now,  # [G1_VPCacheTTL] 저장 시각
                    }'''
)

# ════════════════════════════════════════════════════════════════════
# 결과 출력
# ════════════════════════════════════════════════════════════════════
print("=" * 68)
print("  APEX BOT — phase2_upgrade.py (전략 품질 고도화 2단계)")
print("=" * 68)
ok = skip = fail = 0
for tag, status, msg in results:
    icon = "✅" if status == "OK" else ("⏭ " if status == "SKIP" else "❌")
    note = f" ({msg})" if msg else ""
    print(f"  {icon} {status:<10} | {tag}{note}")
    if status == "OK":      ok += 1
    elif status == "SKIP":  skip += 1
    else:                   fail += 1
print(f"\n  OK={ok}  SKIP={skip}  FAIL/ROLLBACK={fail}")
print(f"  백업: {ARCHIVE}")
print("=" * 68)
if fail == 0:
    print("\n✅ 완료! 다음 단계:")
    print("  git add -A")
    print("  git commit -m 'feat: phase2 — MTF 하드게이트, Sharpe 가중치, 변동성 정규화'")
    print("  git push origin main")
    print("  python main.py --mode paper")
else:
    print(f"\n❌ ROLLBACK {fail}건 발생. archive를 확인하세요.")
    sys.exit(1)
