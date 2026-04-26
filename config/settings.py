"""APEX BOT - ? ?
Pydantic + YAML ? ???"""
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent


@dataclass
class APIConfig:
    """???API ?"""
    access_key: str = field(default_factory=lambda: os.getenv("UPBIT_ACCESS_KEY", ""))
    secret_key: str = field(default_factory=lambda: os.getenv("UPBIT_SECRET_KEY", ""))
    base_url: str = "https://api.upbit.com/v1"
    ws_url: str = "wss://api.upbit.com/websocket/v1"
    rest_limit_per_sec: int = 10
    ws_max_connections: int = 5
    order_limit_per_sec: int = 8


@dataclass
class TradingConfig:
    """?"""
    target_markets: List[str] = field(default_factory=lambda: [
        "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA",
        "KRW-DOGE", "KRW-AVAX", "KRW-DOT", "KRW-LINK", "KRW-ATOM"
    ])
    primary_timeframe: str = "60"
    signal_timeframe: str = "5"
    trend_timeframe: str = "1440"
    available_timeframes: List[str] = field(default_factory=lambda: [
        "1", "5", "15", "60", "240", "1440"
    ])
    order_type: str = "limit"
    min_order_amount: int = 5000
    fee_rate: float = 0.0005
    slippage_rate: float = 0.001
    max_positions: int = 10
    max_dynamic_coins: int = 20  # [FIX] SCR-FASTTRACK 동적 감시 종목 한도
    max_position_ratio: float = 0.20


@dataclass
class RiskConfig:
    """ъ????"""
    max_risk_per_trade: float = 0.015
    kelly_fraction: float = 0.15
    min_position_size: float = 5000
    atr_stop_multiplier: float = 2.0
    atr_target_multiplier: float = 4.0
    trailing_stop_activation: float = 0.020
    trailing_stop_distance: float = 0.015
    daily_loss_limit: float = 0.05
    total_drawdown_limit: float = 0.15
    monthly_loss_limit: float = 0.15
    consecutive_loss_limit: int = 5
    buy_signal_threshold: float = 0.45
    sell_signal_threshold: float = 0.55
    # Phase 8 추가
    regime_bear_max_positions: int = 0       # BEAR 레짐 최대 포지션
    regime_bear_watch_max_ratio: float = 0.5 # BEAR_WATCH 포지션 비율
    surge_min_score: float = 0.60            # Surge 최소 점수
    surge_size_ratio: float = 0.70           # Surge 포지션 크기 비율


@dataclass
class MLConfig:
    """ML ⑤ ?"""
    use_gpu: bool = True
    device: str = "cuda"
    mixed_precision: bool = True
    sequence_length: int = 60
    prediction_horizon: int = 5
    feature_count: int = 120
    hidden_size: int = 256
    num_layers: int = 4
    dropout: float = 0.2
    attention_heads: int = 8
    batch_size: int = 512
    learning_rate: float = 0.001
    epochs: int = 200
    early_stopping_patience: int = 20
    train_ratio: float = 0.7
    val_ratio: float = 0.15
    test_ratio: float = 0.15
    model_save_dir: Path = BASE_DIR / "models" / "saved"
    retrain_interval_hours: int = 168


@dataclass
class StrategyConfig:
    """? ?"""
    enabled_strategies: List[str] = field(default_factory=lambda: [
        # Phase9 백테스트 결과 기반 최적화
        # 유효 전략 (rsi_divergence +6.3%/년, mean_reversion +5.3%/년)
        "RSI_Divergence",     # ★ 최고 성과: 승률 67.4%, 샤프 0.26
        "Williams_R",         # RSI 계열 유지
        "MACD_Cross",         # 보조 (macd_momentum 중립)
        "Bollinger_Squeeze",  # mean_reversion 계열
        "BEAR_REVERSAL",      # 시장 방어용 유지
        "Volume_Profile",     # 보조 지표
        # "Smart_Money",      # Phase9: order_block_smc 성과 부진 (-16.1%/년)
        "Ichimoku_Cloud"      # 추세 보조
    ])
    signal_weight: dict = field(default_factory=lambda: {
        "ML": 0.50,           # Phase9: ML 비중 상향 (val_acc=76.7%)
        "Technical": 0.30,    # rsi_divergence + mean_reversion 중심
        "Volume": 0.12,       # volume_spike 성과 부진으로 축소
        "Sentiment": 0.08     # 유지
    })


@dataclass
class MonitoringConfig:
    """⑤? ?"""
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8888
    telegram_token: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_TOKEN", "")
    )
    telegram_chat_id: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", "")
    )
    log_level: str = "INFO"
    log_dir: Path = BASE_DIR / "logs"
    alert_on_trade: bool = True
    alert_on_error: bool = True
    alert_on_drawdown: bool = True


@dataclass
class DatabaseConfig:
    """??? ?"""
    db_path: Path = BASE_DIR / "database" / "apex_bot.db"
    cache_max_candles: int = 2000
    cache_max_ticks: int = 10000



# ── Phase 9 백테스트 결과 (2026-04-14) ──────────────────────────
# 기간: 2026-03-20 ~ 2026-04-14 (25일, 5코인 x 7전략)
# 최고: rsi_divergence  +6.3%/년 | 승률 67.4% | 샤프 0.26 | MDD 1.2%
# 차선: mean_reversion  +5.3%/년 | 승률 63.1% | 샤프 0.21 | MDD 1.0%
# 제외: order_block_smc -16.1%/년 | trend_following -12.4%/년
# ML 앙상블: val_acc=76.7% (FORWARD_N=8, Phase5)
# ─────────────────────────────────────────────────────────────────
@dataclass
class Settings:
    """? ?"""
    api: APIConfig = field(default_factory=APIConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    ml: MLConfig = field(default_factory=MLConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)

    mode: str = "paper"
    debug: bool = False

    def __post_init__(self):
        env_mode = os.getenv("TRADING_MODE", "").lower()
        if env_mode in ("live", "paper", "backtest"):
            self.mode = env_mode

    def validate(self):
        """Validate settings."""
        if self.mode == "live":
            confirm = os.getenv("APEX_LIVE_CONFIRM", "").lower()
            if confirm != "yes":
                raise RuntimeError(
                    "\n" + "=" * 55 + "\n"
                    "  : ???⑤??? ??? ?\n"
                    "  ?? ????\n"
                    "  APEX_LIVE_CONFIRM=yes\n"
                    "  (?? ?? .env??)\n"
                    + "=" * 55
                )
            assert self.api.access_key, "UPBIT_ACCESS_KEY ?꾩닔"
            assert self.api.secret_key, "UPBIT_SECRET_KEY ?꾩닔"

        assert 0 < self.risk.max_risk_per_trade <= 0.05, (
            "max_risk_per_trade??0~5% ?ъ씠"
        )
        assert self.trading.max_positions >= 1, (
            "max_positions??1 ?댁긽"
        )
        return self


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings().validate()
    return _settings