"""
APEX BOT - 理쒖쥌 ?꾨왂 留ㅽ븨
?쇰큺 1??+ 4?쒓컙遊?6媛쒖썡 諛깊뀒?ㅽ듃 寃곌낵 醫낇빀 (2026-04-05)

?듭떖 寃곕줎:
  - ?꾩옱 ?쒖옣(洹밸떒??怨듯룷, ?섎씫???먯꽌???먯떎 理쒖냼?붽? 紐⑺몴
  - volume_spike + rsi_divergence 媛 ?섎씫???앹〈 ?꾨왂
  - volatility_breakout / order_block_smc ???꾩옱 ?쒖옣 遺?곹빀
  - ?곸듅???꾪솚 ??trend_following + ml_strategy ?쒖꽦???덉젙
"""

# 肄붿씤蹂?理쒖쟻 ?꾨왂 (?꾩옱 ?섎씫??湲곗?)
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
    "Ichimoku_Cloud": {
        "class": "IchimokuCloudStrategy",
        "module": "strategies.trend.ichimoku_cloud",
        "weight": 0.1,
        "category": "advanced"
    },
    "Fibonacci_Retracement": {
        "class": "FibonacciRetracementStrategy",
        "module": "strategies.mean_reversion.fibonacci_retracement",
        "weight": 0.08,
        "category": "advanced"
    },
    "Volume_Spike": {
        "class": "VolumeSpikeDetector",
        "module": "strategies.volume.volume_spike",
        "weight": 0.12,
        "category": "advanced"
    },
    "Market_Regime": {
        "class": "MarketRegimeDetector",
        "module": "strategies.regime.market_regime",
        "weight": 0.1,
        "category": "advanced"
    },
}

# ?곸듅???꾪솚 ???ъ슜???꾨왂 (EMA200 ??+ ADX > 25 議곌굔)
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

# ?댁텧 ?꾨왂 (?꾩옱 ?쒖옣 遺?곹빀)
BLACKLIST_STRATEGIES = ["volatility_breakout", "order_block_smc"]

# ?꾨왂 ?깃낵 ?붿빟 (4h 6媛쒖썡 湲곗?, ?ㅽ봽 理쒓퀬媛?
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
    """?쒖옣 援?㈃???곕씪 肄붿씤蹂?理쒖쟻 ?꾨왂 諛섑솚"""
    if is_bull:
        return BULL_MARKET_MAP.get(market, ["trend_following", "ml_strategy"])
    return COIN_STRATEGY_MAP.get(market, ["rsi_divergence", "volume_spike"])


def is_blacklisted(strategy: str) -> bool:
    """?꾩옱 ?쒖옣 遺?곹빀 ?꾨왂 ?щ?"""
    return strategy in BLACKLIST_STRATEGIES
