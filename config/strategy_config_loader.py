import json, pathlib, os
from loguru import logger
from datetime import datetime

_CONFIG_PATH = pathlib.Path(__file__).parent / "optimized_params.json"
_cache = {}
_cache_mtime = 0.0

def load_config() -> dict:
    """optimized_params.json 을 읽어 캐시 반환 (파일 변경 시 자동 갱신)"""
    global _cache, _cache_mtime
    try:
        mtime = _CONFIG_PATH.stat().st_mtime
        if mtime != _cache_mtime:
            _cache = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            _cache_mtime = mtime
            logger.info(f"[ConfigLoader] optimized_params.json 로드 완료 "
                        f"({len(_cache.get('strategies',{}))}개 전략)")
    except Exception as e:
        logger.warning(f"[ConfigLoader] config 로드 실패: {e}")
    return _cache

def get_strategy_cfg(name: str) -> dict:
    """전략 이름으로 개별 설정 반환"""
    cfg = load_config()
    return cfg.get("strategies", {}).get(name, {})

def is_strategy_active(name: str, current_hour: int = None) -> bool:
    """전략 활성 여부 + 시간 필터 동시 체크"""
    s = get_strategy_cfg(name)
    if not s:
        return True   # config 없으면 허용
    if not s.get("is_active", True):
        return False
    tf = s.get("time_filter", {})
    if tf.get("enabled", False) and current_hour is not None:
        allowed = tf.get("allowed_hours", list(range(24)))
        if current_hour not in allowed:
            return False
    return True

def get_boost(name: str, current_hour: int = None) -> float:
    """전략 boost 반환 (시간대 boost_multiplier 자동 적용)"""
    s = get_strategy_cfg(name)
    if not s:
        return 1.0
    boost = float(s.get("boost", 1.0))
    tf = s.get("time_filter", {})
    if tf.get("enabled", False) and current_hour is not None:
        bh = tf.get("boost_hours", [])
        if current_hour in bh:
            boost *= float(tf.get("boost_multiplier", 1.0))
    return boost

def get_min_confidence(name: str) -> float:
    """전략별 최소 confidence 반환"""
    s = get_strategy_cfg(name)
    return float(s.get("min_confidence", 0.50)) if s else 0.50

def get_ensemble_weights() -> dict:
    """앙상블 가중치 base 값 반환"""
    cfg = load_config()
    weights = cfg.get("ensemble", {}).get("strategy_weights", {})
    return {k: v["base"] for k, v in weights.items()}

def get_regime_boost(strategy: str, regime: str) -> float:
    """레짐별 boost 반환"""
    cfg = load_config()
    boosts = cfg.get("ensemble", {}).get("regime_boosts", {})
    return float(boosts.get(regime, {}).get(strategy, 1.0))

def get_global(key: str, default=None):
    """글로벌 설정값 반환"""
    cfg = load_config()
    return cfg.get("global", {}).get(key, default)

def get_risk(key: str, default=None):
    """리스크 설정값 반환"""
    cfg = load_config()
    return cfg.get("risk", {}).get(key, default)