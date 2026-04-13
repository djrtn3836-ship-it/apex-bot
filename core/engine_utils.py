"""
core/engine_utils.py
────────────────────
엔진 헬퍼 유틸리티 함수 모음
- _floor_vol       : 수량 내림 처리
- _ceil_vol        : 수량 올림 처리  
- calc_position_size : 포지션 크기 계산
- calc_exit_plan   : SL/TP 계획 계산
- _find_free_port  : 포트 탐색
"""
from __future__ import annotations
import math, socket
from typing import TYPE_CHECKING

def _floor_vol(market: str, vol: float) -> float:
    d = _UPBIT_VOL_PREC.get(market, 4)
    f = 10 ** d
    return _math.floor(vol * f) / f

def _ceil_vol(market: str, vol: float) -> float:
    d = _UPBIT_VOL_PREC.get(market, 4)
    f = 10 ** d
    return _math.ceil(vol * f) / f

MIN_POSITION_KRW  = 20_000
MAX_POSITION_RATE = 0.20
MIN_ORDER_KRW     = 5_000


def calc_position_size(
    total_capital: float,
    kelly_f: float,
    current_price: float,
    atr: float,
    open_positions: int,
    max_positions: int,
    signal_score: float = 0.7,
    market: str = "",
) -> dict:
    base_amount = total_capital * kelly_f

    if atr and current_price > 0:
        vol_ratio  = atr / current_price
        target_vol = 0.02
        vol_adj    = min(target_vol / (vol_ratio + 1e-9), 2.0)
        vol_adj    = max(vol_adj, 0.3)
        base_amount *= vol_adj
        vol_note = f"변동성조정×{vol_adj:.2f}"
    else:
        vol_note = "변동성조정없음"

    if signal_score >= 0.85:
        sig_mult = 1.0
        sig_note = "강신호×1.0"
    elif signal_score >= 0.65:
        sig_mult = 0.6
        sig_note = "보통신호×0.6"
    else:
        sig_mult = 0.35
        sig_note = "약신호×0.35"
    base_amount *= sig_mult

    position_ratio = open_positions / max(max_positions, 1)
    conc_mult      = max(1.0 - position_ratio * 0.5, 0.4)
    base_amount   *= conc_mult
    conc_note      = f"집중도×{conc_mult:.2f}"

    base_amount = max(base_amount, MIN_POSITION_KRW)
    base_amount = min(base_amount, total_capital * MAX_POSITION_RATE)

    available   = total_capital - (open_positions * MIN_POSITION_KRW)
    base_amount = min(base_amount, available * 0.9)
    base_amount = max(base_amount, MIN_POSITION_KRW)

    volume = _floor_vol(market, base_amount / current_price) if current_price > 0 else 0

    return {
        "amount_krw":    base_amount,
        "volume":        volume,
        "sizing_reason": (
            f"Kelly={kelly_f:.3f} | {vol_note} | {sig_note} | "
            f"{conc_note} | 최종=₩{base_amount:,.0f}"
        ),
    }


def calc_exit_plan(entry_price: float, atr: float, position_krw: float) -> dict:
    atr_mult = atr if atr else entry_price * 0.02

    sl   = entry_price - atr_mult * 1.5
    tp1  = entry_price + atr_mult * 1.5
    tp2  = entry_price + atr_mult * 3.0
    tp3  = entry_price + atr_mult * 5.0
    trail = 0.015

    if position_krw >= 100_000:
        partial_ratios = [0.25, 0.25, 0.25]
        trail = 0.01
    elif position_krw >= 40_000:
        partial_ratios = [0.30, 0.30]
        trail = 0.015
    elif position_krw >= 20_000:
        partial_ratios = [0.50]
        trail = 0.02
    else:
        partial_ratios = []
        trail = 0.025

    return {
        "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "trail_pct": trail,
        "partial_ratios": partial_ratios,
    }



def _find_free_port(start_port: int = 8888) -> int:
    import socket as _s
    port = start_port
    while port < start_port + 100:
        try:
            with _s.socket(_s.AF_INET, _s.SOCK_STREAM) as sock:
                sock.bind(('', port))
                return port
        except OSError:
            port += 1
    return start_port

class TradingEngine:
    """APEX BOT   v2.0.0"""

    VERSION = "2.0.0"