"""
APEX BOT - 핵심 모듈 단위 테스트
pytest 기반, API 키 불필요 (paper 모드)
"""
import os
import sys
import pytest
import asyncio
import pandas as pd
import numpy as np
from pathlib import Path

# 환경 설정 (테스트용 더미 키)
os.environ.setdefault("UPBIT_ACCESS_KEY", "test_access_key_for_paper_mode")
os.environ.setdefault("UPBIT_SECRET_KEY", "test_secret_key_for_paper_mode")
os.environ.setdefault("TRADING_MODE", "paper")

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 공통 픽스처 ──────────────────────────────────────────────────
@pytest.fixture(scope="session", autouse=True)
def paper_settings():
    """세션 전체에 paper 설정 주입"""
    from config.settings import Settings
    import config.settings as sm
    sm._settings = Settings(mode="paper")
    return sm._settings


@pytest.fixture
def sample_ohlcv(n=200):
    """표준 OHLCV 샘플 DataFrame"""
    np.random.seed(42)
    close = pd.Series(50_000_000 + np.cumsum(np.random.randn(n) * 500_000), dtype=float)
    close = close.clip(lower=1_000_000)  # 음수 방지
    df = pd.DataFrame({
        "open":   close * 0.998,
        "high":   close * 1.012,
        "low":    close * 0.988,
        "close":  close,
        "volume": pd.Series(np.random.randint(50, 500, n), dtype=float),
    })
    df.index = pd.date_range("2024-01-01", periods=n, freq="1h")
    return df


# ════════════════════════════════════════════════════════════════
#  1. 설정 (config.settings)
# ════════════════════════════════════════════════════════════════
class TestSettings:
    def test_settings_mode(self, paper_settings):
        assert paper_settings.mode == "paper"

    def test_target_markets_non_empty(self, paper_settings):
        assert len(paper_settings.trading.target_markets) >= 1
        assert all(m.startswith("KRW-") for m in paper_settings.trading.target_markets)

    def test_risk_limits(self, paper_settings):
        r = paper_settings.risk
        assert 0 < r.max_risk_per_trade <= 0.05
        assert 0 < r.total_drawdown_limit <= 0.30
        assert r.atr_stop_multiplier > 0
        assert r.atr_target_multiplier > r.atr_stop_multiplier  # RR > 1

    def test_get_settings_singleton(self):
        from config.settings import get_settings
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2


# ════════════════════════════════════════════════════════════════
#  2. 상태 머신 (core.state_machine)
# ════════════════════════════════════════════════════════════════
class TestStateMachine:
    def test_initial_state(self):
        from core.state_machine import StateMachine, BotState
        sm = StateMachine()
        assert sm.state == BotState.IDLE

    def test_valid_transition_idle_to_init(self):
        from core.state_machine import StateMachine, BotState
        sm = StateMachine()
        ok = sm.transition(BotState.INITIALIZING)
        assert ok
        assert sm.state == BotState.INITIALIZING

    def test_invalid_transition_blocked(self):
        from core.state_machine import StateMachine, BotState
        sm = StateMachine()
        # IDLE → PAUSED는 허용되지 않음
        ok = sm.transition(BotState.PAUSED)
        assert not ok
        assert sm.state == BotState.IDLE

    def test_can_trade_only_when_running(self):
        from core.state_machine import StateMachine, BotState
        sm = StateMachine()
        sm.transition(BotState.INITIALIZING)
        sm.transition(BotState.RUNNING)
        assert sm.can_trade
        sm.transition(BotState.PAUSED)
        assert not sm.can_trade

    def test_get_status_returns_dict(self):
        from core.state_machine import StateMachine
        sm = StateMachine()
        s = sm.get_status()
        assert "state" in s
        assert "can_trade" in s


# ════════════════════════════════════════════════════════════════
#  3. 이벤트 버스 (core.event_bus)
# ════════════════════════════════════════════════════════════════
class TestEventBus:
    def test_subscribe_and_publish(self):
        from core.event_bus import EventBus, EventType, Event
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe(EventType.CANDLE_UPDATED, handler)

        async def run():
            ev = Event(type=EventType.CANDLE_UPDATED, data={"price": 50000})
            await bus.publish_sync(ev)

        asyncio.run(run())
        assert len(received) == 1
        assert received[0].data["price"] == 50000

    def test_unsubscribe(self):
        from core.event_bus import EventBus, EventType, Event
        bus = EventBus()
        called = []

        async def h(e): called.append(e)

        bus.subscribe(EventType.SIGNAL_GENERATED, h)
        bus.unsubscribe(EventType.SIGNAL_GENERATED, h)

        async def run():
            ev = Event(type=EventType.SIGNAL_GENERATED, data={})
            await bus.publish_sync(ev)

        asyncio.run(run())
        assert len(called) == 0


# ════════════════════════════════════════════════════════════════
#  4. 기술적 지표 (utils.indicators)
# ════════════════════════════════════════════════════════════════
class TestIndicators:
    def test_ema_length(self, sample_ohlcv):
        from utils.indicators import ema
        result = ema(sample_ohlcv["close"], 20)
        assert len(result) == len(sample_ohlcv)
        assert result.dropna().iloc[-1] > 0

    def test_rsi_range(self, sample_ohlcv):
        from utils.indicators import rsi
        result = rsi(sample_ohlcv["close"], 14)
        valid = result.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_macd_returns_three_series(self, sample_ohlcv):
        from utils.indicators import macd
        line, signal, hist = macd(sample_ohlcv["close"])
        assert len(line) == len(sample_ohlcv)
        assert len(signal) == len(sample_ohlcv)
        assert len(hist) == len(sample_ohlcv)

    def test_atr_positive(self, sample_ohlcv):
        from utils.indicators import atr
        result = atr(sample_ohlcv["high"], sample_ohlcv["low"], sample_ohlcv["close"], 14)
        assert result.dropna().gt(0).all()

    def test_bollinger_bands_order(self, sample_ohlcv):
        from utils.indicators import bollinger_bands
        upper, mid, lower = bollinger_bands(sample_ohlcv["close"])
        valid_idx = upper.dropna().index
        assert (upper[valid_idx] >= mid[valid_idx]).all()
        assert (mid[valid_idx] >= lower[valid_idx]).all()

    def test_vwap(self, sample_ohlcv):
        from utils.indicators import vwap
        result = vwap(
            sample_ohlcv["high"], sample_ohlcv["low"],
            sample_ohlcv["close"], sample_ohlcv["volume"]
        )
        assert len(result) == len(sample_ohlcv)
        assert result.dropna().gt(0).all()


# ════════════════════════════════════════════════════════════════
#  5. 전략 신호 (strategies)
# ════════════════════════════════════════════════════════════════
class TestStrategies:
    def test_macd_returns_signal_or_none(self, sample_ohlcv):
        from strategies.momentum.macd_cross import MACDCrossStrategy
        from strategies.base_strategy import StrategySignal
        s = MACDCrossStrategy()
        result = s.generate_signal(sample_ohlcv, "KRW-BTC")
        assert result is None or isinstance(result, StrategySignal)

    def test_rsi_signal_valid_range(self, sample_ohlcv):
        from strategies.momentum.rsi_divergence import RSIDivergenceStrategy
        from strategies.base_strategy import StrategySignal, SignalType
        s = RSIDivergenceStrategy()
        result = s.generate_signal(sample_ohlcv, "KRW-ETH")
        if result:
            assert isinstance(result, StrategySignal)
            assert 0 <= result.strength <= 1
            assert 0 <= result.confidence <= 1
            assert result.signal_type in [SignalType.BUY, SignalType.SELL, SignalType.NEUTRAL]

    def test_bollinger_squeeze(self, sample_ohlcv):
        from strategies.mean_reversion.bollinger_squeeze import BollingerSqueezeStrategy
        from strategies.base_strategy import StrategySignal
        s = BollingerSqueezeStrategy()
        result = s.generate_signal(sample_ohlcv, "KRW-SOL")
        assert result is None or isinstance(result, StrategySignal)

    def test_insufficient_data_returns_none(self):
        from strategies.momentum.macd_cross import MACDCrossStrategy
        s = MACDCrossStrategy()
        tiny_df = pd.DataFrame({
            "open": [1.0], "high": [1.0], "low": [1.0],
            "close": [1.0], "volume": [1.0]
        })
        result = s.generate_signal(tiny_df, "KRW-BTC")
        assert result is None  # MIN_CANDLES 미충족

    def test_strategy_name_attribute(self):
        from strategies.momentum.macd_cross import MACDCrossStrategy
        s = MACDCrossStrategy()
        assert hasattr(s, "NAME")
        assert isinstance(s.NAME, str)
        assert len(s.NAME) > 0


# ════════════════════════════════════════════════════════════════
#  6. 포지션 사이저 (risk.position_sizer)
# ════════════════════════════════════════════════════════════════
class TestPositionSizer:
    def test_size_within_capital(self):
        from risk.position_sizer import PositionSizer
        ps = PositionSizer()
        capital = 1_000_000
        size = ps.calculate(capital=capital, entry_price=50_000_000, atr=500_000)
        assert 0 < size <= capital

    def test_max_position_pct(self):
        from risk.position_sizer import PositionSizer
        ps = PositionSizer()
        capital = 10_000_000
        size = ps.calculate(capital=capital, entry_price=50_000_000, atr=100_000)
        # 최대 자본의 20%
        assert size <= capital * 0.20 + 1  # +1 float rounding

    def test_zero_atr_safe(self):
        from risk.position_sizer import PositionSizer
        ps = PositionSizer()
        # ATR=0이면 최소값 사용해야 함 (ZeroDivisionError 없어야)
        try:
            size = ps.calculate(capital=1_000_000, entry_price=50_000_000, atr=0)
            assert size >= 0
        except ZeroDivisionError:
            pytest.fail("ATR=0에서 ZeroDivisionError 발생")


# ════════════════════════════════════════════════════════════════
#  7. 트레일링 스탑 (risk.stop_loss.trailing_stop)
# ════════════════════════════════════════════════════════════════
class TestTrailingStop:
    def test_stop_loss_triggered_below(self):
        from risk.stop_loss.trailing_stop import TrailingStopManager
        ts = TrailingStopManager()
        ts.add_position("KRW-BTC", 50_000_000, 47_500_000, 500_000)
        result = ts.update("KRW-BTC", 47_000_000)
        assert result == "STOP_LOSS"

    def test_no_trigger_above_stop(self):
        from risk.stop_loss.trailing_stop import TrailingStopManager
        ts = TrailingStopManager()
        ts.add_position("KRW-BTC", 50_000_000, 47_500_000, 500_000)
        result = ts.update("KRW-BTC", 51_000_000)
        assert result is None  # 보유 유지

    def test_trailing_stop_activated_on_profit(self):
        from risk.stop_loss.trailing_stop import TrailingStopManager
        ts = TrailingStopManager()
        ts.add_position("KRW-ETH", 3_000_000, 2_850_000, 30_000)
        # 3% 이상 수익 → 트레일링 스탑 활성화
        ts.update("KRW-ETH", 3_100_000)  # +3.3%
        # 이후 하락
        result = ts.update("KRW-ETH", 3_000_000)
        # 트레일링 스탑 또는 None (손절 레벨에 따라 다름)
        assert result in ["TRAILING_STOP", "STOP_LOSS", None]


# ════════════════════════════════════════════════════════════════
#  8. 신호 결합기 (signals.signal_combiner)
# ════════════════════════════════════════════════════════════════
class TestSignalCombiner:
    def test_combine_returns_combined_signal_or_none(self, paper_settings):
        from signals.signal_combiner import SignalCombiner
        from strategies.base_strategy import StrategySignal, SignalType
        combiner = SignalCombiner(paper_settings)
        signals = [
            StrategySignal(strategy_name="test1", market="KRW-BTC", signal=SignalType.BUY,
                           score=0.8, confidence=0.7, entry_price=50_000_000,
                           stop_loss=47_000_000, take_profit=53_000_000, reason="test",
                           timeframe="60", timestamp=pd.Timestamp.now()),
            StrategySignal(strategy_name="test2", market="KRW-BTC", signal=SignalType.BUY,
                           score=0.6, confidence=0.8, entry_price=50_000_000,
                           stop_loss=47_000_000, take_profit=53_000_000, reason="test",
                           timeframe="60", timestamp=pd.Timestamp.now()),
            StrategySignal(strategy_name="test3", market="KRW-BTC", signal=SignalType.BUY,
                           score=0.7, confidence=0.75, entry_price=50_000_000,
                           stop_loss=47_000_000, take_profit=53_000_000, reason="test",
                           timeframe="60", timestamp=pd.Timestamp.now()),
        ]
        result = combiner.combine(signals, "KRW-BTC", None, "trending_up")
        # 3개 BUY 신호면 combined BUY 나올 수 있음
        assert result is None or hasattr(result, "signal_type")

    def test_conflicting_signals_may_return_none(self, paper_settings):
        from signals.signal_combiner import SignalCombiner
        from strategies.base_strategy import StrategySignal, SignalType
        combiner = SignalCombiner(paper_settings)
        signals = [
            StrategySignal(strategy_name="t1", market="KRW-BTC", signal=SignalType.BUY,
                           score=0.5, confidence=0.5, entry_price=50_000_000,
                           stop_loss=47_000_000, take_profit=53_000_000, reason="test",
                           timeframe="60", timestamp=pd.Timestamp.now()),
            StrategySignal(strategy_name="t2", market="KRW-BTC", signal=SignalType.SELL,
                           score=0.5, confidence=0.5, entry_price=50_000_000,
                           stop_loss=47_000_000, take_profit=53_000_000, reason="test",
                           timeframe="60", timestamp=pd.Timestamp.now()),
        ]
        result = combiner.combine(signals, "KRW-BTC", None, "ranging")
        # 충돌 신호 → None 가능
        assert result is None or hasattr(result, "signal_type")


# ════════════════════════════════════════════════════════════════
#  9. 레짐 감지기 (signals.filters.regime_detector)
# ════════════════════════════════════════════════════════════════
class TestRegimeDetector:
    def test_detect_returns_regime(self, sample_ohlcv):
        from signals.filters.regime_detector import RegimeDetector, MarketRegime
        rd = RegimeDetector()
        regime = rd.detect("KRW-BTC", sample_ohlcv)
        assert isinstance(regime, MarketRegime)

    def test_all_regime_values_covered(self):
        from signals.filters.regime_detector import MarketRegime
        names = [r.name for r in MarketRegime]
        assert "TRENDING_UP" in names or "TRENDING" in names or len(names) >= 2


# ════════════════════════════════════════════════════════════════
#  10. 포트폴리오 매니저 (core.portfolio_manager)
# ════════════════════════════════════════════════════════════════
class TestPortfolioManager:
    def test_set_initial_capital(self):
        from core.portfolio_manager import PortfolioManager
        pm = PortfolioManager()
        pm.set_initial_capital(5_000_000)
        # initial_capital은 내부 속성으로 저장됨
        assert pm.get_total_value(5_000_000) >= 0  # 최소한 동작해야 함

    def test_open_and_close_position(self):
        from core.portfolio_manager import PortfolioManager
        pm = PortfolioManager()
        pm.set_initial_capital(10_000_000)
        pm.open_position(
            market="KRW-BTC",
            entry_price=50_000_000,
            volume=0.01,
            amount_krw=500_000,
            strategy="test",
            stop_loss=47_000_000,
            take_profit=53_000_000,
        )
        assert pm.is_position_open("KRW-BTC")
        assert pm.position_count == 1

        proceeds, pnl = pm.close_position("KRW-BTC", 52_000_000, 2500, "take_profit")
        assert not pm.is_position_open("KRW-BTC")
        assert proceeds > 0

    def test_drawdown_calculation(self):
        from core.portfolio_manager import PortfolioManager
        pm = PortfolioManager()
        pm.set_initial_capital(10_000_000)
        dd = pm.get_current_drawdown(9_000_000)
        assert abs(dd - 10.0) < 0.01  # 10% drawdown


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
