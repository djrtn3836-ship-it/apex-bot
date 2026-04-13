"""
core/engine.py
─────────────────────────────────────────────────────────────
APEX BOT 트레이딩 엔진 v3.0.0

Mixin 구조로 분리된 모듈:
    engine_utils.py    : 헬퍼 유틸리티 함수
    engine_cycle.py    : 메인 사이클 / 포지션 관리 / 시장 스캐너
    engine_buy.py      : 매수 분석 및 실행
    engine_sell.py     : 매도 실행
    engine_ml.py       : ML / PPO 예측 및 모델 관리
    engine_db.py       : DB 포지션 복원 / 쿨다운 관리
    engine_schedule.py : 스케줄 작업 / WS 재연결 / 대시보드

변경 이력:
    v3.0.0 - Mixin 패턴으로 구조 분리 (코드 품질 향상)
    v2.0.1 - _check_circuit_breaker() 수정
             _cycle() ML 배치 직접매수 제거 (중복 방지)
─────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import asyncio
import time
from datetime import datetime
from typing import Dict, List, Optional
from concurrent.futures import ProcessPoolExecutor
from loguru import logger
from core.smart_wallet import SmartWalletManager
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config.settings import get_settings
from core.event_bus import EventBus, EventType
from core.state_machine import BotState, StateMachine
from core.market_regime import GlobalMarketRegimeDetector, GlobalRegime
from core.portfolio_manager import PortfolioManager
from data.collectors.ws_collector import WebSocketCollector
from data.collectors.rest_collector import RestCollector
from data.processors.candle_processor import CandleProcessor
from data.processors.mtf_processor import MTFProcessor
from strategies.order_block_detector import OrderBlockDetector, OrderBlockSignal as OBSignal
from signals.filters.trend_filter import TrendFilter
from signals.filters.volume_profile import VolumeProfileAnalyzer
from data.storage.db_manager import DatabaseManager
from data.storage.cache_manager import CacheManager
from execution.upbit_adapter import UpbitAdapter
from execution.executor import OrderExecutor, ExecutionRequest, OrderSide
from risk.risk_manager import RiskManager
from risk.position_sizer import KellyPositionSizer
from risk.stop_loss.trailing_stop import TrailingStopManager
from risk.stop_loss.atr_stop import ATRStopLoss, StopLevels
from risk.partial_exit import PartialExitManager
from signals.signal_combiner import SignalCombiner, CombinedSignal

try:
    from execution.live_guard import LiveGuard, LiveGuardConfig
    LIVE_GUARD_OK = True
except ImportError:
    LIVE_GUARD_OK = False
try:
    from signals.mtf_signal_merger import MTFSignalMerger, TFDirection
    MTF_MERGER_OK = True
except ImportError:
    MTF_MERGER_OK = False
try:
    from risk.position_manager_v2 import PositionManagerV2, PositionV2, ExitReason
    POS_MGR_V2_OK = True
except ImportError:
    POS_MGR_V2_OK = False
try:
    from monitoring.analytics.strategy_analyzer import StrategyAnalyzer
    STRATEGY_ANALYZER_OK = True
except ImportError:
    STRATEGY_ANALYZER_OK = False
try:
    from monitoring.analytics.live_readiness import LiveReadinessChecker
    LIVE_READINESS_OK = True
except ImportError:
    LIVE_READINESS_OK = False

from signals.filters.regime_detector import RegimeDetector, MarketRegime
from signals.filters.correlation_filter import CorrelationFilter
from signals.filters.kimchi_premium import KimchiPremiumMonitor
from signals.filters.fear_greed import FearGreedMonitor
from signals.filters.volume_spike import VolumeSpikeDetector
from signals.filters.news_sentiment import NewsSentimentAnalyzer
from signals.filters.elliott_wave import ElliottWaveDetectorAnalyzer
from signals.filters.orderbook_signal import OrderbookSignalAnalyzer
from strategies.base_strategy import SignalType
from monitoring.dashboard import DashboardServer, update_dashboard
from models.train.auto_trainer import AutoTrainer
from models.train.ppo_online_trainer import PPOOnlineTrainer
from monitoring.performance_tracker import PerformanceTracker
from monitoring.telegram_bot import TelegramNotifier
from utils.logger import setup_logger, log_trade, log_signal, log_risk
from utils.helpers import now_kst, Timer
from utils.gpu_utils import setup_gpu, maybe_compile, log_gpu_status, clear_gpu_cache
from utils.cpu_optimizer import (
    create_strategy_pool, create_io_thread_pool,
    pin_main_thread_to_pcores, optimize_asyncio_event_loop,
    log_cpu_status,
)
from monitoring.paper_report import generate_paper_report
import math as _math

# ── 볼륨 정밀도 테이블 ────────────────────────────────────────
_UPBIT_VOL_PREC = {
    "KRW-BTC": 8, "KRW-ETH": 8, "KRW-SOL": 4, "KRW-XRP": 4,
    "KRW-ADA": 2, "KRW-DOGE": 2, "KRW-DOT": 4, "KRW-LINK": 4,
    "KRW-AVAX": 4, "KRW-ATOM": 4, "KRW-BORA": 0, "KRW-SUI": 4,
}

# ── Mixin 임포트 ──────────────────────────────────────────────
from core.engine_utils import _floor_vol, _ceil_vol, calc_position_size, calc_exit_plan, _find_free_port
from core.engine_cycle import EngineCycleMixin
from core.engine_buy import EngineBuyMixin
from core.engine_sell import EngineSellMixin
from core.engine_ml import EngineMLMixin
from core.engine_db import EngineDBMixin
from core.engine_schedule import EngineScheduleMixin


class TradingEngine(
    EngineCycleMixin,
    EngineBuyMixin,
    EngineSellMixin,
    EngineMLMixin,
    EngineDBMixin,
    EngineScheduleMixin,
):
    """
    APEX BOT 트레이딩 엔진 v3.0.0
    ─────────────────────────────
    Mixin 패턴으로 분리된 모듈형 구조.
    각 Mixin은 self를 통해 공유 상태에 접근합니다.
    """

    VERSION = "3.0.0"

    def __init__(self):
        self.settings = get_settings()

        self.state_machine   = StateMachine()
        self.event_bus       = EventBus()
        self.portfolio       = PortfolioManager()
        self.regime_detector = RegimeDetector()
        self.global_regime_detector = GlobalMarketRegimeDetector()
        self.signal_combiner = SignalCombiner(self.settings)

        self.ws_collector      = None
        self.rest_collector    = RestCollector()
        self.candle_processor  = CandleProcessor()
        self.db_manager        = DatabaseManager()
        self.cache_manager     = CacheManager()

        self.adapter        = UpbitAdapter()
        self.executor       = OrderExecutor(self.adapter)
        self.risk_manager   = RiskManager()
        self.position_sizer = KellyPositionSizer()
        self.trailing_stop  = TrailingStopManager()

        self.atr_stop       = ATRStopLoss()
        self.mtf_processor  = MTFProcessor()
        self.trend_filter   = TrendFilter()
        self.volume_profile = VolumeProfileAnalyzer()
        self.auto_trainer   = AutoTrainer()
        self.perf_tracker   = PerformanceTracker()
        self.partial_exit   = PartialExitManager()

        self.live_guard = LiveGuard() if LIVE_GUARD_OK else None
        if self.live_guard:
            logger.info(" LiveGuard (M2) 초기화")

        self.mtf_merger = MTFSignalMerger() if MTF_MERGER_OK else None
        if self.mtf_merger:
            logger.info(" MTFSignalMerger (M3) 초기화")

        self.position_mgr_v2 = PositionManagerV2(
            max_hold_hours=72, breakeven_trigger=0.02,
            partial_exit_1=0.03, partial_exit_1_pct=0.30,
            partial_exit_2=0.05, partial_exit_2_pct=0.30,
            pyramid_max=2, pyramid_trigger=0.02,
        ) if POS_MGR_V2_OK else None
        if self.position_mgr_v2:
            logger.info(" PositionManagerV2 (M4) 초기화")

        self.strategy_analyzer = StrategyAnalyzer()     if STRATEGY_ANALYZER_OK else None
        self.live_readiness    = LiveReadinessChecker() if LIVE_READINESS_OK    else None
        if self.strategy_analyzer:
            logger.info(" StrategyAnalyzer (M7) 초기화")

        self.correlation_filter = CorrelationFilter()
        self.kimchi_monitor     = KimchiPremiumMonitor()
        self.fear_greed         = FearGreedMonitor()
        self.volume_spike       = VolumeSpikeDetector()

        try:
            from data.processors.orderbook_analyzer import OrderBookAnalyzer
            self.orderbook_analyzer = OrderBookAnalyzer()
            logger.info(" OrderBookAnalyzer  ")
        except Exception as _ob_err:
            self.orderbook_analyzer = None
            import traceback
            logger.error(f" OrderBookAnalyzer  : {_ob_err}")
            logger.error(traceback.format_exc())

        try:
            from strategies.order_block_detector import OrderBlockDetector
            self.ob_detector = OrderBlockDetector(impulse_mult=2.0, lookback=100)
            logger.info(" OrderBlockDetector  ")
        except Exception as _obd_err:
            self.ob_detector = None
            logger.warning(f" OrderBlockDetector  : {_obd_err}")

        try:
            from core.rate_limit_manager import RateLimitManager
            self.rate_limiter = RateLimitManager()
        except Exception as _rl_err:
            self.rate_limiter = None
            logger.warning(f" RateLimitManager  : {_rl_err}")

        try:
            from core.slippage_model import SlippageModel
            self.slippage_model = SlippageModel()
        except Exception as _sm_err:
            self.slippage_model = None
            logger.warning(f" SlippageModel  : {_sm_err}")

        self.news_analyzer = NewsSentimentAnalyzer(use_finbert=True)
        self.dashboard     = DashboardServer()
        self.telegram      = TelegramNotifier()
        self.scheduler     = AsyncIOScheduler(timezone="Asia/Seoul")
        self._process_pool = create_strategy_pool()

        self._strategies      = {}
        self._ml_predictor    = None
        self._ppo_agent       = None

        try:
            self.ppo_online_trainer = PPOOnlineTrainer()
            logger.info(" PPOOnlineTrainer  ")
        except Exception as _ppo_e:
            self.ppo_online_trainer = None
            logger.warning(f" PPOOnlineTrainer  : {_ppo_e}")

        self._market_prices:     Dict[str, float] = {}
        self._last_signal_time:  Dict[str, float] = {}
        self._sell_cooldown:     Dict[str, datetime] = {}  # market -> sell_time, prevent rebuy for 10min
        self._ml_predictions: dict = {}  # ML 예측 캐시
        self._signal_cooldown    = 300
        self._device             = "cpu"
        self._buying_markets:    set = set()
        self._selling_markets:   set = set()
        self._ml_batch_cache:    dict = {}

        self._wallet = SmartWalletManager()

        self._SCANNER_CONFIG = {
            "interval_sec":      30,
            "vol_surge_ratio":   3.0,
            "price_change_min":  0.02,
            "min_trade_amount":  50_000_000,
            "max_dynamic_coins": 20,
            "exclude_markets":   [],
        }
        self._dynamic_markets: list  = []
        self._last_scan_time:  float = 0.0
        self.markets:          list  = []
        self.markets = self.settings.trading.target_markets
        logger.info(f" APEX BOT v{self.VERSION}  ")

    # ── 시작 / 종료 ──────────────────────────────────────────────

    async def start(self):
        setup_logger(
            self.settings.monitoring.log_level,
            self.settings.monitoring.log_dir,
        )
        logger.info("=" * 60)
        logger.info(f"  APEX BOT v{self.VERSION} ")
        logger.info(f"  : {self.settings.mode.upper()}")
        logger.info(f"  : {len(self.settings.trading.target_markets)}개 코인")
        logger.info("=" * 60)

        try:
            self.state_machine.transition(BotState.INITIALIZING)
            await self.db_manager.initialize()
            self.executor.db_manager = self.db_manager

            await self.adapter.initialize()
            krw_balance = await self.adapter.get_balance("KRW")
            self.portfolio.set_initial_capital(krw_balance)
            logger.info(f"  : ₩{krw_balance:,.0f}")

            await self._restore_positions_from_db()
            await self._restore_sl_cooldown()
            self._load_strategies()

            self._device = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: setup_gpu(
                    use_gpu=self.settings.ml.use_gpu,
                    benchmark=True,
                    tf32=True,
                )
            )

            await self._load_ml_model()
            await self._init_ppo_agent()
            await self._init_external_data()

            try:
                self.dashboard.setup(engine_ref=self)
                await self.dashboard.start()
                logger.info('  Dashboard  : http://0.0.0.0:' + str(
                    _find_free_port(self.settings.monitoring.dashboard_port)))
            except Exception as _dash_err:
                logger.warning(' Dashboard   (  ): ' + str(_dash_err))
                logger.warning('  Dashboard   Bot  ')

            await self.telegram.initialize(engine_ref=self)

            self._register_schedules()

            self.scheduler.add_job(
                self._run_auto_retrain, "cron", hour=3, minute=0,
                id="auto_retrain", replace_existing=True,
            )
            self.scheduler.add_job(
                self._run_backtest_all, "cron", hour=3, minute=0,
                id="backtest_v2_daily", replace_existing=True,
            )
            self.scheduler.start()

            self.state_machine.transition(BotState.RUNNING)
            await update_dashboard({"type": "status", "status": "RUNNING"})

            async def _on_ws_message(data):
                msg_type = data.get("ty", data.get("type", ""))
                market   = data.get("cd", data.get("code", ""))
                if msg_type == "ticker":
                    price = data.get("tp", data.get("trade_price", 0))
                    if market and price:
                        self._market_prices[market] = price
                        self.correlation_filter.update_price(market, price)
                        self.kimchi_monitor.update_upbit_price(market, price)
                elif msg_type == "orderbook":
                    if market:
                        raw_units  = data.get("obu", data.get("orderbook_units", []))
                        normalized = {
                            "market":           market,
                            "timestamp":        data.get("tms", 0),
                            "total_ask_size":   data.get("tas", 0.0),
                            "total_bid_size":   data.get("tbs", 0.0),
                            "orderbook_units":  [
                                {
                                    "ask_price": u.get("ap", u.get("ask_price", 0)),
                                    "bid_price": u.get("bp", u.get("bid_price", 0)),
                                    "ask_size":  u.get("as", u.get("ask_size",  0)),
                                    "bid_size":  u.get("bs", u.get("bid_size",  0)),
                                }
                                for u in raw_units
                            ],
                        }
                        self.cache_manager.set_orderbook(market, normalized)

            self.ws_collector = WebSocketCollector(
                markets=self.settings.trading.target_markets,
                on_message=_on_ws_message,
            )
            self.ws_collector.subscribe_ticker()
            self.ws_collector.subscribe_orderbook()
            logger.info(
                f" WebSocket    | "
                f"{len(self.settings.trading.target_markets)}개 코인"
            )

            await self._initial_data_fetch()
            logger.info("   ")
            await self._main_loop()

        except KeyboardInterrupt:
            logger.info("   ")
        except Exception as e:
            logger.error(f"   : {e}")
            await self.telegram.notify_error(str(e), "메인 루프")
            raise
        finally:
            await self.stop()


    async def stop(self):
        logger.info(" APEX BOT  ...")
        self.state_machine.transition(BotState.STOPPED)
        self.scheduler.shutdown(wait=False)
        self._process_pool.shutdown(wait=False)
        if self.ws_collector:
            await self.ws_collector.stop()
        await self.dashboard.stop()
        logger.info(" APEX BOT  ")


    def pause(self):
        self.state_machine.transition(BotState.PAUSED)
        log_risk("PAUSE", "신규 거래 일시 중단")
        asyncio.create_task(
            self.telegram.notify_risk("PAUSE", "신규 거래 일시 중단")
        )


    def resume(self):
        self.state_machine.transition(BotState.RUNNING)
        logger.info("  ")

    # ── 외부 데이터 초기화 ───────────────────────────────────────

    async def _init_external_data(self):
        logger.info("    ...")
        tasks = [
            self.kimchi_monitor.fetch_all(),
            self.fear_greed.fetch(),
            self.news_analyzer.fetch_news(),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        names   = ["김치 프리미엄", "공포탐욕 지수", "뉴스 감성"]
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.warning(f"{names[i]}   ( ): {r}")
        logger.info(
            f"   : {self.fear_greed.index} ({self.fear_greed.label})"
        )
        logger.info(
            f"   : "
            f"{results[2] if not isinstance(results[2], Exception) else 0}건"
        )

    # ── Circuit Breaker ──────────────────────────────────────────