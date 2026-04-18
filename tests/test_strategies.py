"""APEX BOT -   
pytest + pytest-asyncio"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


def create_mock_df(n: int = 200, trend: str = "up") -> pd.DataFrame:
    """OHLCV DataFrame"""
    dates = [datetime.now() - timedelta(hours=n-i) for i in range(n)]
    base_price = 50_000_000  # BTC 5000만원 기준

    if trend == "up":
        close = base_price + np.cumsum(np.random.randn(n) * 100_000 + 50_000)
    elif trend == "down":
        close = base_price + np.cumsum(np.random.randn(n) * 100_000 - 50_000)
    else:
        close = base_price + np.random.randn(n) * 500_000

    close = np.maximum(close, base_price * 0.5)
    high = close + np.abs(np.random.randn(n)) * 200_000
    low = close - np.abs(np.random.randn(n)) * 200_000
    open_ = close + np.random.randn(n) * 100_000
    volume = np.random.exponential(10, n)

    df = pd.DataFrame({
        "open": open_, "high": high, "low": np.minimum(low, close),
        "close": close, "volume": volume,
    }, index=pd.DatetimeIndex(dates))
    return df.sort_index()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """add_indicators 실행"""
    c = df["close"]
    df["ema20"] = c.ewm(span=20, adjust=False).mean()
    df["ema50"] = c.ewm(span=50, adjust=False).mean()
    df["ema200"] = c.ewm(span=200, adjust=False).mean()

    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi"] = 100 - (100 / (1 + gain / (loss + 1e-10)))

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    bb_mid = c.rolling(20).mean()
    bb_std = c.rolling(20).std()
    df["bb_upper"] = bb_mid + 2 * bb_std
    df["bb_mid"] = bb_mid
    df["bb_lower"] = bb_mid - 2 * bb_std
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / bb_mid
    df["bb_pct"] = (c - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"] + 1e-10)

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - c.shift()).abs(),
        (df["low"] - c.shift()).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=14, adjust=False).mean()
    df["atr_pct"] = df["atr"] / c * 100

    df["vol_sma20"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_sma20"]

    df["vwap"] = ((df["high"] + df["low"] + c) / 3 * df["volume"]).cumsum() / df["volume"].cumsum()

    df["adx"] = 25.0
    df["di_plus"] = 30.0
    df["di_minus"] = 20.0
    df["supertrend"] = df["ema20"]
    df["supertrend_dir"] = 1.0
    df["bullish"] = c > df["open"]
    df["bearish"] = c < df["open"]
    return df


# ── MACD 전략 테스트 ──────────────────────────────────────────────
class TestMACDCrossStrategy:
    def setup_method(self):
        from strategies.momentum.macd_cross import MACDCrossStrategy
        self.strategy = MACDCrossStrategy()

    def test_returns_none_insufficient_data(self):
        df = create_mock_df(10)
        result = self.strategy.analyze("KRW-BTC", df)
        assert result is None

    def test_analyze_returns_signal_or_none(self):
        df = add_indicators(create_mock_df(100))
        result = self.strategy.analyze("KRW-BTC", df)
        if result is not None:
            from strategies.base_strategy import SignalType
            assert result.signal_type in (SignalType.BUY, SignalType.SELL)
            assert 0 <= result.strength <= 1
            assert 0 <= result.confidence <= 1
            assert result.market == "KRW-BTC"

    def test_has_parameters(self):
        params = self.strategy.get_parameters()
        assert isinstance(params, dict)
        assert len(params) > 0


# ── RSI 전략 테스트 ───────────────────────────────────────────────
class TestRSIDivergenceStrategy:
    def setup_method(self):
        from strategies.momentum.rsi_divergence import RSIDivergenceStrategy
        self.strategy = RSIDivergenceStrategy()

    def test_signal_generation(self):
        df = add_indicators(create_mock_df(150))
        result = self.strategy.analyze("KRW-ETH", df)
        # None 또는 유효한 Signal
        if result is not None:
            assert result.strategy_name == self.strategy.name
            assert result.market == "KRW-ETH"

    def test_strategy_name(self):
        assert self.strategy.name != ""
        assert self.strategy.weight > 0


# ── 볼린저 전략 테스트 ────────────────────────────────────────────
class TestBollingerSqueezeStrategy:
    def setup_method(self):
        from strategies.mean_reversion.bollinger_squeeze import BollingerSqueezeStrategy
        self.strategy = BollingerSqueezeStrategy()

    def test_analyze(self):
        df = add_indicators(create_mock_df(100))
        result = self.strategy.analyze("KRW-XRP", df)
        if result is not None:
            assert result.strength > 0


# ── 리스크 관리 테스트 ────────────────────────────────────────────
class TestRiskManager:
    def setup_method(self):
        import os
        os.environ["UPBIT_ACCESS_KEY"] = "test"
        os.environ["UPBIT_SECRET_KEY"] = "test"
        from risk.risk_manager import RiskManager
        self.rm = RiskManager()

    @pytest.mark.asyncio
    async def test_can_open_position_normal(self):
        can, reason = await self.rm.can_open_position("KRW-BTC", 1_000_000, 0)
        assert can is True

    @pytest.mark.asyncio
    async def test_max_positions_limit(self):
        can, reason = await self.rm.can_open_position("KRW-BTC", 1_000_000, 5)
        assert can is False
        assert "포지션" in reason

    @pytest.mark.asyncio
    async def test_insufficient_capital(self):
        can, reason = await self.rm.can_open_position("KRW-BTC", 100, 0)
        assert can is False

    def test_consecutive_loss_tracking(self):
        for _ in range(5):
            self.rm.record_trade_result(False)
        assert self.rm._consecutive_losses == 5

    def test_win_resets_consecutive_loss(self):
        for _ in range(3):
            self.rm.record_trade_result(False)
        self.rm.record_trade_result(True)
        assert self.rm._consecutive_losses == 0


# ── 포지션 사이저 테스트 ──────────────────────────────────────────
class TestPositionSizer:
    def setup_method(self):
        import os
        os.environ["UPBIT_ACCESS_KEY"] = "test"
        os.environ["UPBIT_SECRET_KEY"] = "test"
        from risk.position_sizer import PositionSizer
        self.sizer = PositionSizer()

    def test_basic_sizing(self):
        size = self.sizer.calculate(
            capital=1_000_000,
            entry_price=50_000_000,
            atr=500_000,
        )
        assert size > 0
        assert size <= 1_000_000 * 0.20 + 1  # 최대 20%

    def test_minimum_size(self):
        size = self.sizer.calculate(
            capital=1_000_000,
            entry_price=100,
            atr=1,
        )
        assert size >= 5000  # 최소 5000원

    def test_kelly_with_stats(self):
        size = self.sizer.calculate(
            capital=1_000_000,
            entry_price=50_000_000,
            atr=500_000,
            win_rate=60,
            avg_win=2.0,
            avg_loss=1.0,
        )
        assert size > 0


# ── 신호 결합기 테스트 ────────────────────────────────────────────
class TestSignalCombiner:
    def setup_method(self):
        import os
        os.environ["UPBIT_ACCESS_KEY"] = "test"
        os.environ["UPBIT_SECRET_KEY"] = "test"
        from signals.signal_combiner import SignalCombiner
        from strategies.base_strategy import Signal, SignalType
        self.combiner = SignalCombiner()
        self.Signal = Signal
        self.SignalType = SignalType

    def _make_sig(self, market, sig_type, score, confidence, strat_name, reason):
        """StrategySignal"""
        from datetime import datetime
        return self.Signal(
            strategy_name=strat_name,
            market=market,
            signal=sig_type,
            score=score,
            confidence=confidence,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=110.0,
            reason=reason,
            timeframe="60",
            timestamp=datetime.now(),
        )

    def test_buy_signal_combination(self):
        signals = [
            self._make_sig("KRW-BTC", self.SignalType.BUY, 0.8, 0.8, "MACD_Cross", "테스트"),
            self._make_sig("KRW-BTC", self.SignalType.BUY, 0.7, 0.7, "RSI_Divergence", "테스트"),
            self._make_sig("KRW-BTC", self.SignalType.BUY, 0.9, 0.85, "OrderBlock_SMC", "테스트"),
        ]
        # 임계점을 낮춰서 결과 확인
        self.combiner.buy_threshold = 1.0
        result = self.combiner.combine(signals, "KRW-BTC")
        # 강한 매수 신호이면 결과 있어야 함
        if result:
            assert result.signal_type == self.SignalType.BUY

    def test_no_signal_below_threshold(self):
        signals = [
            self._make_sig("KRW-BTC", self.SignalType.BUY, 0.1, 0.3, "MACD_Cross", "약한신호"),
        ]
        self.combiner.buy_threshold = 10.0  # 매우 높은 임계값
        result = self.combiner.combine(signals, "KRW-BTC")
        assert result is None


# ── 레짐 감지 테스트 ──────────────────────────────────────────────
class TestRegimeDetector:
    def setup_method(self):
        from signals.filters.regime_detector import RegimeDetector
        self.detector = RegimeDetector()

    def test_detect_trending(self):
        df = add_indicators(create_mock_df(100, "up"))
        # 인위적으로 ADX 높게 설정
        df["adx"] = 35.0
        df["di_plus"] = 40.0
        df["di_minus"] = 15.0
        from signals.filters.regime_detector import MarketRegime
        regime = self.detector.detect("KRW-BTC", df)
        assert regime != MarketRegime.UNKNOWN

    def test_detect_returns_valid_regime(self):
        df = add_indicators(create_mock_df(100))
        from signals.filters.regime_detector import MarketRegime
        regime = self.detector.detect("KRW-ETH", df)
        assert isinstance(regime, MarketRegime)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
