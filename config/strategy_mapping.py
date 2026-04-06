"""
APEX BOT - 최종 전략 매핑
일봉 1년 + 4시간봉 6개월 백테스트 결과 종합 (2026-04-05)

핵심 결론:
  - 현재 시장(극단적 공포, 하락장)에서는 손실 최소화가 목표
  - volume_spike + rsi_divergence 가 하락장 생존 전략
  - volatility_breakout / order_block_smc 는 현재 시장 부적합
  - 상승장 전환 시 trend_following + ml_strategy 활성화 예정
"""

# 코인별 최적 전략 (현재 하락장 기준)
COIN_STRATEGY_MAP = {
    "KRW-BTC":  ["ml_strategy",    "rsi_divergence"],
    "KRW-ETH":  ["volume_spike",   "rsi_divergence"],
    "KRW-XRP":  ["rsi_divergence", "volume_spike"],
    "KRW-SOL":  ["rsi_divergence", "mean_reversion"],
    "KRW-ADA":  ["rsi_divergence", "volume_spike"],
    "KRW-DOGE": ["rsi_divergence", "volume_spike"],
    "KRW-DOT":  ["volume_spike",   "rsi_divergence"],
    "KRW-LINK": ["rsi_divergence", "mean_reversion"],
    "KRW-AVAX": ["rsi_divergence", "volume_spike"],
    "KRW-ATOM": ["volume_spike",   "rsi_divergence"],
}

# 상승장 전환 시 사용할 전략 (EMA200 위 + ADX > 25 조건)
BULL_MARKET_MAP = {
    "KRW-BTC":  ["ml_strategy",     "trend_following"],
    "KRW-ETH":  ["trend_following", "volatility_breakout"],
    "KRW-XRP":  ["trend_following", "macd_momentum"],
    "KRW-SOL":  ["trend_following", "ml_strategy"],
    "KRW-ADA":  ["trend_following", "volume_spike"],
    "KRW-DOGE": ["volume_spike",    "macd_momentum"],
    "KRW-DOT":  ["rsi_divergence",  "trend_following"],
    "KRW-LINK": ["trend_following", "mean_reversion"],
    "KRW-AVAX": ["trend_following", "mean_reversion"],
    "KRW-ATOM": ["mean_reversion",  "trend_following"],
}

# 퇴출 전략 (현재 시장 부적합)
BLACKLIST_STRATEGIES = ["volatility_breakout", "order_block_smc"]

# 전략 성과 요약 (4h 6개월 기준, 샤프 최고값)
STRATEGY_BEST_SHARPE = {
    "rsi_divergence":    {"best_coin": "ETH",  "sharpe": 0.104, "timeframe": "4h"},
    "volume_spike":      {"best_coin": "ETH",  "sharpe": 0.451, "timeframe": "4h"},
    "ml_strategy":       {"best_coin": "BTC",  "sharpe": 0.743, "timeframe": "1d"},
    "trend_following":   {"best_coin": "AVAX", "sharpe": 1.284, "timeframe": "1d"},
    "mean_reversion":    {"best_coin": "AVAX", "sharpe": 0.786, "timeframe": "1d"},
    "macd_momentum":     {"best_coin": "ETH",  "sharpe": 0.530, "timeframe": "1d"},
    "order_block_smc":   {"best_coin": "ATOM", "sharpe": 0.224, "timeframe": "1d"},
    "volatility_breakout": {"best_coin": "ETH","sharpe": 0.594, "timeframe": "1d"},
}


def get_strategy(market: str, is_bull: bool = False) -> list:
    """시장 국면에 따라 코인별 최적 전략 반환"""
    if is_bull:
        return BULL_MARKET_MAP.get(market, ["trend_following", "ml_strategy"])
    return COIN_STRATEGY_MAP.get(market, ["rsi_divergence", "volume_spike"])


def is_blacklisted(strategy: str) -> bool:
    """현재 시장 부적합 전략 여부"""
    return strategy in BLACKLIST_STRATEGIES
