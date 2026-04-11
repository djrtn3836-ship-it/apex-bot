"""APEX BOT -     v2.0.0
    

  :
       (TrailingStopManager)
     (PartialExitManager) - 3  
     (CorrelationFilter) - BTC    
      (KimchiPremiumMonitor)
      (FearGreedMonitor)
      (VolumeSpikeDetector)
   GPU  (RTX 50xx CUDA )
   24h    

 :
  v2.0.1 - _check_circuit_breaker()   
           _cycle() ML     (  )"""
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

_UPBIT_VOL_PREC = {
    "KRW-BTC": 8, "KRW-ETH": 8, "KRW-SOL": 4, "KRW-XRP": 4,
    "KRW-ADA": 2, "KRW-DOGE": 2, "KRW-DOT": 4, "KRW-LINK": 4,
    "KRW-AVAX": 4, "KRW-ATOM": 4, "KRW-BORA": 0, "KRW-SUI": 4,
}

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

    def __init__(self):
        self.settings = get_settings()

        self.state_machine   = StateMachine()
        self.event_bus       = EventBus()
        self.portfolio       = PortfolioManager()
        self.regime_detector = RegimeDetector()
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
    async def _check_circuit_breaker(self) -> bool:
        """. True=.

         FIX v2.0.1:    
                  hasattr   1  →"""
        try:
            import datetime as _dt
            daily_loss_limit = getattr(
                self.settings.risk, "daily_loss_limit", 0.05
            )

            krw = (
                self.adapter._paper_balance.get("KRW", 0)
                if self.adapter.is_paper else 0
            )
            current = self.portfolio.get_total_value(krw)
            now     = _dt.datetime.now()

            # ✅ FIX: 최초 초기화
            if not hasattr(self, "_day_start_balance"):
                self._day_start_balance = current
                self._day_start_date    = now.date()
                return False

            # ✅ FIX: 자정이 지나면 기준값 리셋
            if now.date() != self._day_start_date:
                logger.info(
                    f"   |  : "
                    f"₩{self._day_start_balance:,.0f} → ₩{current:,.0f}"
                )
                self._day_start_balance = current
                self._day_start_date    = now.date()
                return False

            if self._day_start_balance <= 0:
                return False

            loss_pct = (
                (self._day_start_balance - current)
                / self._day_start_balance
            )

            if loss_pct >= daily_loss_limit:
                logger.warning(
                    f" Circuit Breaker ! "
                    f"  {loss_pct:.1%} "
                    f"( {daily_loss_limit:.1%}) "
                    f"— 신규 매수 차단"
                )
                return True

            return False

        except Exception as _e:
            logger.error(f"[circuit_breaker] {_e}")
            return False

    # ── 메인 루프 ────────────────────────────────────────────────
    async def _main_loop(self):
        while self.state_machine.state != BotState.STOPPED:
            try:
                if self.state_machine.state == BotState.RUNNING:
                    with Timer("메인 루프 사이클"):
                        if await self._check_circuit_breaker():
                            await asyncio.sleep(60)
                            continue
                        await self._cycle()
                elif self.state_machine.state == BotState.PAUSED:
                    logger.debug("⏸  ...")
                    await asyncio.sleep(10)
                    continue
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f" : {e}")
                await asyncio.sleep(30)

    async def _cycle(self):
        """FIX v2.0.1: ML       
                :  →   (   )
                :   →  (   )"""
        _base    = list(self.settings.trading.target_markets)
        _dynamic = [
            m for m in getattr(self, "_dynamic_markets", [])
            if m not in _base
        ]
        markets      = _base + _dynamic
        self.markets = markets

        price_tasks = [self.adapter.get_current_price(m) for m in markets]
        prices      = await asyncio.gather(*price_tasks, return_exceptions=True)

        for i, market in enumerate(markets):
            if isinstance(prices[i], Exception) or prices[i] is None:
                continue
            p = prices[i]
            self._market_prices[market] = p
            self.correlation_filter.update_price(market, p)
            self.kimchi_monitor.update_upbit_price(market, p)

        self.portfolio.update_prices(self._market_prices)

        krw         = await self.adapter.get_balance("KRW")
        total_value = self.portfolio.get_total_value(krw)
        drawdown    = self.portfolio.get_current_drawdown(total_value)

        if await self.risk_manager.check_circuit_breaker(drawdown, total_value):
            return

        try:
            new_surge_markets = await self._market_scanner()
            if new_surge_markets:
                _ws_added = []
                for _sm in new_surge_markets:
                    if _sm not in markets:
                        markets.append(_sm)
                        self.markets = markets
                        _ws_added.append(_sm)
                        logger.info(f"    : {_sm}")
                if _ws_added and hasattr(self, "ws_collector") and self.ws_collector:
                    try:
                        added = self.ws_collector.add_markets(_ws_added)
                        if added:
                            await self.ws_collector.resubscribe()
                    except Exception as _ws_e:
                        logger.debug(f"WebSocket   : {_ws_e}")
        except Exception as _se:
            logger.debug(f"  : {_se}")

        await self._check_time_based_exits()
        await self._check_position_exits()

        # ✅ FIX: ML 배치 추론을 분석 실행 전에 먼저 수행
        try:
            _batch_df_map = {}
            for _bm in markets:
                _bdf = self.cache_manager.get_ohlcv(_bm)
                if _bdf is not None and len(_bdf) >= 60:
                    _batch_df_map[_bm] = _bdf
            if _batch_df_map:
                _batch_results       = await self._get_ml_prediction_batch(_batch_df_map)
                self._ml_batch_cache = _batch_results  # ✅ 분석 전에 저장
            else:
                self._ml_batch_cache = {}
        except Exception as _be:
            logger.debug(f" ML  : {_be}")
            self._ml_batch_cache = {}

        # ===== v2.1.0 시그널 평가 (ML 배치 캐시 기반) =====
        if self._ml_batch_cache:
            logger.debug(f"    ({len(self._ml_batch_cache)}개 코인)")
            for market, ml_pred in self._ml_batch_cache.items():
                try:
                    if self.portfolio.is_position_open(market):
                        logger.debug(f"{market}    - ")
                        continue
                    df = self.cache_manager.get_ohlcv(market)
                    if df is None or len(df) < 60:
                        logger.debug(f"{market}   ({len(df) if df is not None else 0}개)")
                        continue
                    ml_score = ml_pred.get('confidence', 0)
                    ml_signal = ml_pred.get('signal', 'UNKNOWN')
                    logger.debug(f"{market} ML={ml_score:.3f} ={ml_signal}")
                    if ml_score > 0.1:
                        logger.info(f" {market}    (ML={ml_score:.3f})")
                        signal = await self._evaluate_entry_signals(market, df, ml_score)
                        if signal and signal.get('action') == 'BUY':
                            logger.info(f" {market}   ! ML={ml_score:.3f}")
                            await self._execute_buy(market, signal, df)
                        elif signal is None:
                            logger.debug(f"{market}  ")
                    else:
                        logger.debug(f"{market} ML   ({ml_score:.3f})")
                except Exception as e:
                    logger.error(f"{market}   : {e}", exc_info=True)
        else:
            logger.debug("ML   -   ")
        # ===============================




        logger.debug(f" [v2.1.0] ML  : {len(self._ml_batch_cache)} | 내용: {list(self._ml_batch_cache.keys()) if self._ml_batch_cache else 'EMPTY'}")



        new_entry_markets = [
            m for m in markets if not self.portfolio.is_position_open(m)
        ]
        existing_markets = [
            m for m in markets if self.portfolio.is_position_open(m)
        ]
        can_enter_new = (
            self.portfolio.position_count < self.settings.trading.max_positions
            and krw >= self.settings.trading.min_order_amount
        )
        entry_tasks = (
            [self._analyze_market(m) for m in new_entry_markets]
            if can_enter_new else []
        )
        exist_tasks = [
            self._analyze_existing_position(m) for m in existing_markets
        ]
        await asyncio.gather(*(entry_tasks + exist_tasks), return_exceptions=True)

        try:
            _ml_market = "KRW-BTC"
            _ml_df     = None
            try:
                _ml_df = self.cache_manager.get_candles(_ml_market, "1h")
            except Exception:
                pass
            if _ml_df is None or len(_ml_df) < 10:
                try:
                    _ml_df = self.cache_manager.get_candles(_ml_market, "1d")
                except Exception:
                    pass
            if _ml_df is None or len(_ml_df) < 10:
                for _attr in ["_df_cache", "_candle_cache", "_ohlcv_cache"]:
                    _cache = getattr(self, _attr, None)
                    if _cache and isinstance(_cache, dict):
                        _ml_df = (
                            _cache.get(f"{_ml_market}-1h")
                            or _cache.get(_ml_market)
                        )
                        if _ml_df is not None:
                            break
            if _ml_df is not None and len(_ml_df) >= 50:
                _ml_result = await self._get_ml_prediction(_ml_market, _ml_df)
                if _ml_result:
                    from monitoring.dashboard import dashboard_state
                    _sig  = _ml_result.get("signal",     "HOLD")
                    _conf = _ml_result.get("confidence", 0.0)
                    _bp   = _ml_result.get("buy_prob",   0.0)
                    _sp   = _ml_result.get("sell_prob",  0.0)
                    dashboard_state.signals["ml_prediction"] = {
                        "signal":     _sig,
                        "confidence": round(float(_conf), 3),
                        "buy_prob":   round(float(_bp),   3),
                        "sell_prob":  round(float(_sp),   3),
                        "market":     _ml_market,
                    }
                    dashboard_state.signals["ml_predictions"] = {
                        _ml_market: dashboard_state.signals["ml_prediction"]
                    }
        except Exception:
            pass

        await self._update_dashboard_state(krw, total_value)
    # ── 시간기반 강제청산 ────────────────────────────────────────
    async def _check_time_based_exits(self) -> None:
        import datetime
        now     = datetime.datetime.now()
        markets = list(self.portfolio.open_positions.keys())

        for market in markets:
            try:
                pos = self.portfolio.get_position(market)
                if not pos:
                    continue
                current_price = self._market_prices.get(market)
                if not current_price or current_price <= 0:
                    continue
                entry_time = (
                    getattr(pos, "entry_time",  None)
                    or getattr(pos, "created_at", None)
                )
                if entry_time is None:
                    continue
                if isinstance(entry_time, str):
                    try:
                        entry_time = datetime.datetime.fromisoformat(entry_time)
                    except Exception:
                        continue

                elif isinstance(entry_time, float):
                    entry_time = datetime.datetime.fromtimestamp(entry_time)
                held_hours  = (now - entry_time).total_seconds() / 3600
                profit_rate = (current_price - pos.entry_price) / pos.entry_price

                if held_hours >= 72:
                    logger.info(
                        f" 72h  ({market}): "
                        f"보유={held_hours:.1f}h | 수익={profit_rate*100:.2f}%"
                    )
                    await self._execute_sell(market, "시간초과_72h_강제청산", current_price)
                    continue

                if held_hours >= 48 and -0.01 <= profit_rate <= 0.01:
                    logger.info(
                        f" 48h  ({market}): "
                        f"보유={held_hours:.1f}h | 수익={profit_rate*100:.2f}%"
                    )
                    await self._execute_sell(market, "횡보청산_48h", current_price)
                    continue

                if held_hours >= 24 and profit_rate <= -0.02:
                    logger.info(
                        f" 24h  ({market}): "
                        f"보유={held_hours:.1f}h | 수익={profit_rate*100:.2f}%"
                    )
                    await self._execute_sell(market, "손실청산_24h", current_price)
                    continue

            except Exception as _te:
                logger.debug(f"   ({market}): {_te}")

    # ── 포지션 청산 체크 ─────────────────────────────────────────
    async def _check_position_exits(self):
        markets = list(self.portfolio.open_positions.keys())
        for market in markets:
            try:
                current_price = self._market_prices.get(market)
                if not current_price:
                    continue
                pos = self.portfolio.get_position(market)
                if pos is None:
                    continue

                entry_price = (
                    getattr(pos, "avg_price",   None)
                    or getattr(pos, "entry_price", None)
                    or (pos.get("avg_price") if isinstance(pos, dict) else None)
                    or 0
                )
                if entry_price <= 0:
                    continue

                try:
                    _df_pos = self.cache_manager.get_ohlcv(market)
                    if _df_pos is not None and len(_df_pos) >= 20:
                        _profit_pct = (current_price - entry_price) / entry_price
                        _sl_levels  = self.atr_stop.get_dynamic_levels(
                            _df_pos, entry_price, current_price, _profit_pct
                        )
                        basic_sl = _sl_levels.stop_loss
                        basic_tp = _sl_levels.take_profit
                        if _profit_pct >= 0.03:
                            logger.info(
                                f" SL   ({market}): "
                                f"SL={basic_sl:,.0f} | "
                                f"수익={_profit_pct*100:.2f}% | "
                                f"RR={_sl_levels.rr_ratio:.2f}"
                            )
                    else:
                        basic_sl = entry_price * (
                            1 - getattr(self.settings.risk, "stop_loss_pct", 0.03)
                        )
                        basic_tp = entry_price * (
                            1 + getattr(self.settings.risk, "take_profit_pct", 0.05)
                        )
                except Exception as _dyn_e:
                    logger.debug(f"ATR    ({market}): {_dyn_e}")
                    basic_sl = entry_price * (
                        1 - getattr(self.settings.risk, "stop_loss_pct", 0.03)
                    )
                    basic_tp = entry_price * (
                        1 + getattr(self.settings.risk, "take_profit_pct", 0.05)
                    )

                if current_price <= basic_sl:
                    loss_pct = (current_price - entry_price) / entry_price * 100
                    logger.info(
                        f"    ({market}): "
                        f"현재={current_price:,.0f} ≤ SL={basic_sl:,.0f} "
                        f"({loss_pct:.2f}%)"
                    )
                    await self._execute_sell(
                        market, f"기본손절_{loss_pct:.1f}%", current_price
                    )
                    continue

                if current_price >= basic_tp:
                    profit_pct = (current_price - entry_price) / entry_price * 100
                    logger.info(
                        f"    ({market}): "
                        f"현재={current_price:,.0f} ≥ TP={basic_tp:,.0f} "
                        f"({profit_pct:.2f}%)"
                    )
                    await self._execute_sell(
                        market, f"기본익절_{profit_pct:.1f}%", current_price
                    )
                    continue

                exit_reason = self.trailing_stop.update(market, current_price)
                if exit_reason:
                    await self._execute_sell(market, exit_reason, current_price)
                    continue

                exit_volume   = self.partial_exit.check(market, current_price)
                _partial_done = False
                if exit_volume > 0:
                    await self._execute_partial_sell(market, exit_volume, current_price)
                    _partial_done = True

                if self.position_mgr_v2 is not None:
                    try:
                        _exit_sig = self.position_mgr_v2.check_exit(market, current_price)
                        if _exit_sig.should_exit:
                            logger.info(
                                f" M4   ({market}): "
                                f"사유={_exit_sig.reason.value} | "
                                f"비율={_exit_sig.volume_pct:.0%} | "
                                f"{_exit_sig.message}"
                            )
                            if _exit_sig.reason.value == "PARTIAL_EXIT":
                                if _partial_done:
                                    logger.debug(
                                        f"⏭ M4   ({market}): "
                                        f"PartialExit 이미 실행됨"
                                    )
                                else:
                                    _pos_v = self.portfolio.open_positions.get(market)
                                    if _pos_v:
                                        _sell_vol = (
                                            getattr(_pos_v, "volume", 0)
                                            * _exit_sig.volume_pct
                                        )
                                        if _sell_vol > 0:
                                            await self._execute_partial_sell(
                                                market, _sell_vol, current_price
                                            )
                            else:
                                await self._execute_sell(
                                    market,
                                    f"M4_{_exit_sig.reason.value}",
                                    current_price,
                                )
                    except Exception as _m4_e:
                        logger.debug(f"M4    ({market}): {_m4_e}")

            except Exception as _e:
                logger.debug(f"    ({market}): {_e}")

    # ── 기존 포지션 재평가 ───────────────────────────────────────
    async def _analyze_existing_position(self, market: str) -> None:
        try:
            pos = self.portfolio.get_position(market)
            if pos is None:
                return

            candles = self.cache_manager.get_ohlcv(market, "1h")
            if candles is None or (hasattr(candles, "__len__") and len(candles) < 20):
                try:
                    candles = await self.rest_collector.get_ohlcv(
                        market, interval="minute60", count=100
                    )
                except Exception:
                    candles = None

            try:
                _candle_len = len(candles) if candles is not None else 0
            except Exception:
                _candle_len = 0
            if _candle_len < 20:
                return

            ml_result = await self._get_ml_prediction(market, candles)
            if ml_result is None:
                return

            signal     = ml_result.get("signal",     "HOLD")
            confidence = ml_result.get("confidence", 0.0)

            if hasattr(pos, "avg_price"):
                entry_price = getattr(pos, "avg_price", 0) or getattr(pos, "entry_price", 0)
            elif hasattr(pos, "entry_price"):
                entry_price = getattr(pos, "entry_price", 0)
            elif isinstance(pos, dict):
                entry_price = pos.get("avg_price", pos.get("entry_price", 0))
            else:
                entry_price = 0

            current_price = self._market_prices.get(market, 0)
            pnl_pct = (
                (current_price - entry_price) / entry_price * 100
                if entry_price > 0 else 0.0
            )

            logger.debug(
                f"   | {market} | "
                f"ML={signal}({confidence:.2f}) | PnL={pnl_pct:+.2f}%"
            )

            if entry_price > 0 and current_price > 0 and _candle_len >= 20:
                try:
                    _profit_pct = (current_price - entry_price) / entry_price
                    _atr_levels = self.atr_stop.get_dynamic_levels(
                        candles, entry_price, current_price, _profit_pct
                    )
                    _basic_sl = _atr_levels.stop_loss
                    _basic_tp = _atr_levels.take_profit

                    if _profit_pct >= 0.03:
                        logger.info(
                            f" SL   ({market}): "
                            f"SL={_basic_sl:,.0f} | "
                            f"수익={_profit_pct*100:.2f}% | "
                            f"RR={_atr_levels.rr_ratio:.2f}"
                        )

                    if current_price <= _basic_sl:
                        _loss_pct = _profit_pct * 100
                        logger.info(
                            f" ATR   ({market}): "
                            f"현재={current_price:,.0f} ≤ SL={_basic_sl:,.0f} "
                            f"({_loss_pct:.2f}%)"
                        )
                        await self._execute_sell(
                            market, f"ATR손절_{_loss_pct:.1f}%", current_price
                        )
                        return

                    if current_price >= _basic_tp:
                        _profit_pct2 = _profit_pct * 100
                        logger.info(
                            f" ATR   ({market}): "
                            f"현재={current_price:,.0f} ≥ TP={_basic_tp:,.0f} "
                            f"({_profit_pct2:.2f}%)"
                        )
                        await self._execute_sell(
                            market, f"ATR익절_{_profit_pct2:.1f}%", current_price
                        )
                        return

                except Exception as _atr_e:
                    logger.debug(f"ATR     ({market}): {_atr_e}")

            if (
                (confidence >= 0.45 and pnl_pct <= -0.3) or
                (confidence >= 0.45 and pnl_pct >= 0.3) or
                (confidence >= 0.42 and pnl_pct >= 1.0) or
                (pnl_pct >= 1.5) or
                (pnl_pct <= -1.5 and confidence >= 0.38) or
                (pnl_pct >= self._time_based_tp_threshold(market))  # [FIX3] 시간 기반 익절
            ):
                logger.info(
                    f" ML   | {market} | "
                    f"={confidence:.2f} | ={pnl_pct:+.2f}%"
                )
                await self._execute_sell(
                    market, f"ML익절_{pnl_pct:.1f}%", current_price
                )
                return

        except Exception as e:
            import traceback
            logger.debug(
                f"   ({market}): {e} | "
                f"{traceback.format_exc().splitlines()[-1]}"
            )

    # ── 신규 마켓 분석 ───────────────────────────────────────────
    async def _analyze_market(self, market: str):
        # Dynamic ML threshold based on Fear & Greed Index (v2.0.4 fixed)
        fgi_idx = getattr(self.fear_greed, 'index', None) or 50
        _base_buy  = self.settings.risk.buy_signal_threshold   # 0.35
        _base_sell = self.settings.risk.sell_signal_threshold  # 0.35
        if fgi_idx < 20:    # Extreme Fear -> lower threshold (easier to buy)
            buy_threshold  = max(0.25, _base_buy  - 0.10)
            sell_threshold = max(0.20, _base_sell - 0.10)
        elif fgi_idx < 40:  # Fear
            buy_threshold  = max(0.30, _base_buy  - 0.05)
            sell_threshold = _base_sell
        elif fgi_idx > 80:  # Extreme Greed -> raise threshold (harder to buy)
            buy_threshold  = _base_buy  + 0.15
            sell_threshold = _base_sell + 0.10
        else:               # Neutral / Greed
            buy_threshold  = _base_buy
            sell_threshold = _base_sell
        from signals.signal_combiner import CombinedSignal, SignalType

        if self.portfolio.position_count >= self.settings.trading.max_positions:
            return
        if self.portfolio.is_position_open(market):
            return

        last_signal = self._last_signal_time.get(market, 0)
        _cooldown   = (
            60 if market in getattr(self, "_bear_reversal_markets", set())
            else self._signal_cooldown
        )
        if time.time() - last_signal < _cooldown:
            return

        try:
            open_pos         = list(self.portfolio.open_positions.keys())
            can_buy_corr, corr_reason = self.correlation_filter.can_buy(market, open_pos)
            if not can_buy_corr:
                logger.debug(f"  ({market}): {corr_reason}")
                return

            can_buy_kimchi, kimchi_reason, premium = self.kimchi_monitor.can_buy(market)
            if not can_buy_kimchi:
                logger.debug(
                    f"   ({market}): {kimchi_reason} "
                    f"[프리미엄 {premium:.1f}%]"
                )
                return

            df_1h = await self.rest_collector.get_ohlcv(market, "minute60", 200)
            if df_1h is None or len(df_1h) < 50:
                return

            try:
                df_1d = await self.rest_collector.get_ohlcv(market, "day", 210)
                if df_1d is None or len(df_1d) < 5:
                    raise ValueError("  ")
                _strategy_hint = (
                    "BEAR_REVERSAL"
                    if market in getattr(self, "_bear_reversal_markets", set())
                    else "default"
                )
                _trend = self.trend_filter.is_buy_allowed(
                    daily_df=df_1d, strategy=_strategy_hint
                )
                if not _trend["allowed"]:
                    logger.debug(
                        f"[TrendFilter]   ({market}): {_trend['reason']}"
                    )
                    return
                logger.debug(
                    f"[TrendFilter] {market}: {_trend['reason']} "
                    f"(={_trend.get('regime', '?')})"
                )
            except Exception as _te:
                logger.debug(f"[TrendFilter]  ({market}): {_te}")

            try:
                _vp = self.volume_profile.analyze(df_1h)
                if _vp is not None:
                    _cur_price = float(df_1h["close"].iloc[-1])
                    _vp_sr     = self.volume_profile.get_nearest_support_resistance(
                        df_1h, _cur_price
                    )
                    _rr  = _vp_sr.get("risk_reward", 1.0)
                    _sup = _vp_sr.get("support",     0)
                    _res = _vp_sr.get("resistance",  0)
                    if _rr < 0.5 and _sup > 0 and _res > 0:
                        logger.info(
                            f"[VolumeProfile]   ({market}): "
                            f"RR={_rr:.2f} 저항={_res:,.0f} 지지={_sup:,.0f}"
                        )
                        return
                    logger.info(
                        f"[VolumeProfile] {market}: "
                        f"POC={_vp.poc_price:,.0f} "
                        f"VAH={_vp.vah:,.0f} VAL={_vp.val:,.0f} RR={_rr:.2f}"
                    )
            except Exception as _ve:
                logger.debug(f"[VolumeProfile]  ({market}): {_ve}")

            df_processed = await self.candle_processor.process(market, df_1h, "60")
            if df_processed is None:
                return

            regime = self.regime_detector.detect(
                market, df_processed,
                fear_greed_index=self.fear_greed.index,
            )

            if regime == MarketRegime.TRENDING_DOWN:
                return
            if regime == MarketRegime.BEAR_REVERSAL:
                logger.info(
                    f" BEAR_REVERSAL  ({market}) → "
                    f"역발상 매수 탐색 (포지션 50% 축소)"
                )
                self._bear_reversal_markets = getattr(
                    self, "_bear_reversal_markets", set()
                )
                self._bear_reversal_markets.add(market)
            else:
                self.bear_reversal_markets = getattr(
                    self, "_bear_reversal_markets", set()
                )
                self.bear_reversal_markets.discard(market)

            is_dumping, dump_reason = self.volume_spike.is_dumping(df_processed, market)
            _is_bear_rev = market in getattr(self, "_bear_reversal_markets", set())
            _in_pyramid  = getattr(self, "_current_pyramid_market", None) == market

            if is_dumping and not _is_bear_rev and not _in_pyramid:
                logger.debug(f"  ({market}): {dump_reason}")
                return
            elif is_dumping and _is_bear_rev:
                logger.debug(
                    f" BEAR_REVERSAL   ({market}): {dump_reason}"
                )

            signals  = await self._run_strategies(market, df_processed)
            ml_pred  = await self._get_ml_prediction(market, df_processed)
            ppo_pred = await self._get_ppo_prediction(market, df_processed)

            if ppo_pred and ml_pred:
                ml_conf  = ml_pred.get("confidence",  0)
                ppo_conf = ppo_pred.get("confidence", 0)
                ml_sig   = ml_pred.get("signal", "HOLD")
                ppo_sig  = str(ppo_pred.get("action", ppo_pred.get("signal", "HOLD"))).upper()
                if ml_sig == ppo_sig:
                    # Agreement: boost confidence but never reduce below ml_conf
                    boosted = ml_conf * 0.7 + ppo_conf * 0.3 + 0.05
                    ml_pred["confidence"]    = min(1.0, max(ml_conf, boosted))
                    ml_pred["ppo_agreement"] = True
                else:
                    # Disagreement: keep ML confidence unchanged (no penalty)
                    ml_pred["confidence"]    = ml_conf
                    ml_pred["ppo_agreement"] = False
                logger.debug(
                    f"ML+PPO  ({market}): "
                    f"ML={ml_pred.get('signal','?')}({ml_conf:.2f}) | "
                    f"PPO={ppo_pred.get('action','?')}({ppo_conf:.2f}) | "
                    f"일치={ml_pred.get('ppo_agreement', False)}"
                )
            elif ppo_pred and ml_pred is None:
                ml_pred = ppo_pred

            fg_adj = self.fear_greed.get_signal_adjustment()
            if fg_adj.get("block_buy", False):
                logger.info(
                    f"   ({market}): "
                    f"지수={self.fear_greed.index} ({self.fear_greed.label})"
                )
                return
            if ml_pred and fg_adj.get("mode") == "suppressed":
                if ml_pred.get("confidence", 0) < 0.35:
                    logger.debug(
                        f"  ({market}): "
                        f"지수={self.fear_greed.index} ({self.fear_greed.label})"
                    )
                    return

            spike_info          = self.volume_spike.detect(df_processed, market)
            vol_confidence_adj  = self.volume_spike.get_confidence_adjustment(spike_info)

            combined = self.signal_combiner.combine(
                signals, market, ml_pred, regime.value
            )

            if combined is None:
                if self.portfolio.position_count >= self.settings.trading.max_positions:
                    return
                if market in getattr(self, "_bear_reversal_markets", set()):
                    _today           = datetime.now().strftime("%Y-%m-%d")
                    _bear_count_key  = f"_bear_rev_count_{_today}"
                    _bear_count      = getattr(self, _bear_count_key, 0)
                    if _bear_count >= 6:
                        logger.info(
                            f" BEAR_REVERSAL    ({market}): "
                            f"{_bear_count}/6 → 강제 BUY 차단"
                        )
                        return
                    _max_p = self.settings.trading.max_positions
                    if self.portfolio.position_count >= int(_max_p * 0.5):
                        logger.info(
                            f" BEAR_REVERSAL  50%  ({market}): "
                            f"{self.portfolio.position_count}/"
                            f"{int(_max_p*0.5)} → 차단"
                        )
                        return
                    if hasattr(self, "_sl_cooldown") and market in self._sl_cooldown:
                        import datetime as _dt2
                        if _dt2.datetime.now() < self._sl_cooldown[market]:
                            remaining = int(
                                (
                                    self._sl_cooldown[market]
                                    - _dt2.datetime.now()
                                ).total_seconds() // 60
                            )
                            logger.info(
                                f"    ({market}): "
                                f"{remaining}분 후 재매수 가능"
                            )
                            return
                        else:
                            del self._sl_cooldown[market]
                    _fg_idx = getattr(self.fear_greed, "index", 50)
                    if _fg_idx > 20:
                        logger.info(
                            f" BEAR_REVERSAL    ({market}): "
                            f"지수={_fg_idx} > 20 → 강제 BUY 차단"
                        )
                        return
                    setattr(self, _bear_count_key, _bear_count + 1)
                    logger.info(
                        f" BEAR_REVERSAL  BUY   ({market}): "
                        f"오늘 {_bear_count+1}/6회"
                    )
                    combined = CombinedSignal(
                        market=market,
                        signal_type=SignalType.BUY,
                        score=0.45,
                        confidence=0.45,
                        agreement_rate=1.0,
                        contributing_strategies=["BEAR_REVERSAL"],
                        reasons=["극단적 공포 역발상 매수"],
                    )

            if combined is None:
                return

            if vol_confidence_adj > 0:
                combined.confidence = min(
                    1.0, combined.confidence * (1 + vol_confidence_adj)
                )
                logger.debug(
                    f"   ({market}): "
                    f"+{vol_confidence_adj:.2%} 신뢰도 향상"
                )

            ob_analyzer = getattr(self, "orderbook_analyzer", None)
            if ob_analyzer is not None:
                try:
                    ob_data    = self.cache_manager.get_orderbook(market)
                    ob_signal  = ob_analyzer.analyze(market, ob_data)
                    can_buy_ob, ob_reason = ob_analyzer.can_buy(ob_signal)
                    if not can_buy_ob and combined.signal_type == SignalType.BUY:
                        logger.debug(f"  ({market}): {ob_reason}")
                        return
                    ob_adj = ob_analyzer.get_confidence_adjustment(
                        ob_signal, trade_side="BUY"
                    )
                    if abs(ob_adj) > 0.01:
                        combined.confidence = min(
                            1.0, combined.confidence * (1 + ob_adj)
                        )
                        logger.debug(
                            f"  ({market}): {ob_adj:+.2%} "
                            f"→ 신뢰도={combined.confidence:.2f}"
                        )
                except Exception as ob_e:
                    logger.debug(f"   ({market}): {ob_e}")
            else:
                logger.debug(f"   ({market}) → 통과")

            can_buy_news, news_reason = self.news_analyzer.can_buy(market)
            if not can_buy_news and combined.signal_type == SignalType.BUY:
                logger.debug(f"   ({market}): {news_reason}")
                return

            news_score, news_boost = self.news_analyzer.get_signal_boost(market)
            if abs(news_boost) > 0.3:
                original_score = combined.score
                combined.score = combined.score - news_boost
                logger.debug(
                    f"   ({market}): "
                    f"{original_score:.2f} → {combined.score:.2f} "
                    f"(boost={news_boost:+.2f}, 감성={news_score:+.3f})"
                )

            log_signal(
                market, combined.signal_type.name,
                combined.score, combined.contributing_strategies
            )

            if self.mtf_merger is not None:
                try:
                    _tf_map = {
                        "1d":  ("day",       "1d"),
                        "4h":  ("minute240", "4h"),
                        "1h":  ("minute60",  "1h"),
                        "15m": ("minute15",  "15m"),
                        "5m":  ("minute5",   "5m"),
                        "1m":  ("minute1",   "1m"),
                    }
                    _tf_data = {}
                    for _tf_key, (_tf_upbit, _tf_cache) in _tf_map.items():
                        # 1) 1h는 이미 처리된 df_processed 재사용 (API 호출 0)
                        if _tf_key == "1h" and df_processed is not None and len(df_processed) >= 5:
                            _tf_data["1h"] = df_processed
                            continue
                        # 2) 5m/1m 은 Rate Limit 절약을 위해 skip
                        if _tf_key in ("5m", "1m"):
                            continue
                        # 3) cache_manager에서 다양한 방법으로 시도
                        _cached = None
                        for _getter in [
                            lambda: self.cache_manager.get_ohlcv(market, _tf_key),
                            lambda: self.cache_manager.get_ohlcv(market, _tf_cache),
                            lambda: self.cache_manager.get_candles(market, _tf_cache),
                            lambda: self.cache_manager.get_candles(market, _tf_key),
                        ]:
                            try:
                                _cached = _getter()
                                if _cached is not None and len(_cached) >= 5:
                                    break
                                _cached = None
                            except Exception:
                                _cached = None
                        if _cached is not None and len(_cached) >= 5:
                            _tf_data[_tf_key] = _cached
                            continue
                        # 4) REST API fallback (1d, 4h 만 추가 요청)
                        if _tf_key in ("1d", "4h"):
                            try:
                                _fetched = await self.rest_collector.get_ohlcv(
                                    market, _tf_upbit, 60
                                )
                                if _fetched is not None and len(_fetched) >= 5:
                                    _tf_data[_tf_key] = _fetched
                            except Exception:
                                pass

                    if _tf_data:
                        _mtf_result = self.mtf_merger.analyze(_tf_data)
                        _mtf_score  = _mtf_result.combined_score
                        _mtf_dir    = _mtf_result.final_direction.value

                        if combined.signal_type == SignalType.BUY:
                            if _mtf_dir <= -1 and not _is_bear_rev:
                                logger.info(
                                    f" MTF  ({market}): "
                                    f"방향={_mtf_result.final_direction.name} | "
                                    f"{_mtf_result.reason}"
                                )
                                return
                            if _mtf_dir >= 1:
                                _boost = min(0.3, abs(_mtf_score) * 0.2)
                                combined.score = min(3.0, combined.score + _boost)
                                logger.info(
                                    f" MTF   ({market}): "
                                    f"+{_boost:.2f} → score={combined.score:.2f} | "
                                    f"TF수={len(_tf_data)}개 | {_mtf_result.reason}"
                                )
                            else:
                                logger.debug(
                                    f"MTF  ({market}): {_mtf_result.reason}"
                                )
                        elif combined.signal_type == SignalType.SELL:
                            if _mtf_dir >= 1:
                                logger.debug(
                                    f"MTF SELL  ({market}): "
                                    f"상위TF 상승중 | {_mtf_result.reason}"
                                )
                except Exception as _mtf_e:
                    logger.debug(f"MTF   ({market}): {_mtf_e}")

            try:
                await self.db_manager.log_signal({
                    "market":      market,
                    "signal_type": combined.signal_type.name,
                    "score":       combined.score,
                    "confidence":  combined.confidence,
                    "strategies":  combined.contributing_strategies,
                    "regime":      getattr(combined, "regime", ""),
                    "executed":    False,
                })
            except Exception as _sig_e:
                logger.debug(f"signal_log DB  : {_sig_e}")

            _is_bear_rev = market in getattr(self, "_bear_reversal_markets", set())
            if _is_bear_rev and combined.signal_type != SignalType.SELL:
                if combined.signal_type != SignalType.BUY:
                    logger.info(
                        f" BEAR_REVERSAL   ({market}): "
                        f"{combined.signal_type.name} → BUY "
                        f"(score={combined.score:.2f})"
                    )
                    combined.signal_type = SignalType.BUY
                    combined.score       = max(combined.score, 0.45)
                    combined.confidence  = max(combined.confidence, 0.45)
                combined.bear_reversal = True

            try:
                _ob_df = self.cache_manager.get_candles(market, "1h")
                if _ob_df is not None and len(_ob_df) >= 30:
                    _ob_price = float(df_processed["close"].iloc[-1])
                    _ob_sig   = self.ob_detector.detect(_ob_df, _ob_price)
                    if (
                        _ob_sig.signal == "SELL_ZONE"
                        and _ob_sig.confidence >= 0.5
                    ):
                        if combined.signal_type == SignalType.BUY:
                            logger.info(
                                f"  SELL_ZONE   ({market}): "
                                f"신뢰도={_ob_sig.confidence:.2f} "
                                f"거리={_ob_sig.dist_bearish_pct:.1f}%"
                            )
                            return
                    if (
                        _ob_sig.signal == "BUY_ZONE"
                        and _ob_sig.confidence >= 0.4
                    ):
                        logger.info(
                            f"  BUY_ZONE ({market}): "
                            f"신뢰도={_ob_sig.confidence:.2f} "
                            f"거리={_ob_sig.dist_bullish_pct:.1f}%"
                        )
            except Exception as _ob_e:
                logger.debug(f"   ({market}): {_ob_e}")

            if combined.signal_type == SignalType.BUY:
                if market not in self.portfolio.open_positions:
                    await self._execute_buy(market, combined, df_processed)
                    self._last_signal_time[market] = time.time()
                else:
                    logger.debug(
                        f"   ({market}) → 중복 매수 스킵"
                    )

        except Exception as e:
            logger.error(f"   ({market}): {e}")

    # ── 전략 선택 / 실행 ────────────────────────────────────────
    def _get_preferred_strategies(self, market: str) -> list:
        BEAR_PREFERRED = {
            "KRW-BTC":  ["macd_cross",       "Supertrend"],
            "KRW-ETH":  ["bollinger_squeeze", "VWAP_Reversion"],
            "KRW-XRP":  ["bollinger_squeeze", "VWAP_Reversion"],
            "KRW-SOL":  ["VWAP_Reversion",    "bollinger_squeeze"],
            "KRW-ADA":  ["bollinger_squeeze", "VWAP_Reversion"],
            "KRW-DOGE": ["bollinger_squeeze", "macd_cross"],
            "KRW-DOT":  ["bollinger_squeeze", "VWAP_Reversion"],
            "KRW-LINK": ["VWAP_Reversion",    "bollinger_squeeze"],
            "KRW-AVAX": ["VWAP_Reversion",    "bollinger_squeeze"],
            "KRW-ATOM": ["bollinger_squeeze",  "VWAP_Reversion"],
        }
        BULL_PREFERRED = {
            "KRW-BTC":  ["macd_cross",       "Supertrend"],
            "KRW-ETH":  ["Supertrend",        "VWAP_Reversion"],
            "KRW-XRP":  ["Supertrend",        "macd_cross"],
            "KRW-SOL":  ["Supertrend",        "macd_cross"],
            "KRW-ADA":  ["Supertrend",        "bollinger_squeeze"],
            "KRW-DOGE": ["bollinger_squeeze", "macd_cross"],
            "KRW-DOT":  ["Supertrend",        "VWAP_Reversion"],
            "KRW-LINK": ["Supertrend",        "VWAP_Reversion"],
            "KRW-AVAX": ["Supertrend",        "VWAP_Reversion"],
            "KRW-ATOM": ["VWAP_Reversion",    "Supertrend"],
        }
        is_bull   = market not in getattr(self, "_bear_reversal_markets", set())
        preferred = (BULL_PREFERRED if is_bull else BEAR_PREFERRED).get(
            market, list(self._strategies.keys())
        )
        available = [n for n in preferred if n in self._strategies]
        if not available:
            available = list(self._strategies.keys())
        return available

    async def _run_strategies(self, market: str, df) -> list:
        signals   = []
        tasks     = []
        preferred = self._get_preferred_strategies(market)
        selected  = {n: s for n, s in self._strategies.items() if n in preferred}
        if not selected:
            selected = self._strategies
        logger.debug(
            f"  ({market}): {list(selected.keys())} "
            f"[전체 {len(self._strategies)}개 중 {len(selected)}개]"
        )
        for name, strategy in selected.items():
            tasks.append(asyncio.get_event_loop().run_in_executor(
                None, strategy.analyze, market, df, {}
            ))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.debug(
                    f"   ({market}): "
                    f"{type(result).__name__}: {result}"
                )
            elif result:
                signals.append(result)
                logger.debug(
                    f"   ({market}): "
                    f"signal={getattr(result,'signal','?')} "
                    f"score={getattr(result,'score',0):.2f} "
                    f"strategy={getattr(result,'strategy_name','?')}"
                )
        if not signals:
            logger.debug(
                f"   ({market}): "
                f"0/{len(selected)}개 전략에서 신호 없음"
            )
        return signals

    # ── ML / PPO 예측 ────────────────────────────────────────────
    async def _get_ml_prediction(self, market: str, df) -> Optional[dict]:
        if self._ml_predictor is None:
            return None
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._ml_predictor.predict, market, df
            )
            if result:
                from monitoring.dashboard import dashboard_state
                from datetime import datetime
                _sig  = result.get("signal",     "HOLD")
                _conf = result.get("confidence", 0.0)
                _bp   = result.get("buy_prob",   0.0)
                _sp   = result.get("sell_prob",  0.0)
                _ml_pred_data = {
                    "signal":     _sig,
                    "confidence": round(float(_conf), 3),
                    "buy_prob":   round(float(_bp),   3),
                    "sell_prob":  round(float(_sp),   3),
                    "market":     market,
                }
                if "ml_predictions" not in dashboard_state.signals:
                    dashboard_state.signals["ml_predictions"] = {}
                dashboard_state.signals["ml_predictions"][market] = {
                    "signal":          result.get("signal"),
                    "confidence":      round(result.get("confidence", 0), 4),
                    "buy_prob":        round(result.get("buy_prob",   0), 4),
                    "hold_prob":       round(result.get("hold_prob",  0), 4),
                    "sell_prob":       round(result.get("sell_prob",  0), 4),
                    "model_agreement": round(result.get("model_agreement", 0), 4),
                    "inference_ms":    round(result.get("inference_ms",    0), 2),
                    "updated_at":      datetime.now().strftime("%H:%M:%S"),
                }
                dashboard_state.signals["ml_predictions"][market] = _ml_pred_data
                dashboard_state.signals["ml_prediction"]           = _ml_pred_data
                dashboard_state.signals["ml_last_updated"] = (
                    datetime.now().isoformat()
                )
                dashboard_state.signals["ml_model_loaded"] = (
                    self._ml_predictor._is_loaded
                )
            return result
        except Exception as e:
            logger.error(f"ML   ({market}): {e}")
            return None

    async def _get_ml_prediction_batch(self, market_df_map: dict) -> dict:
        if self._ml_predictor is None:
            return {}
        try:
            t_start = __import__("time").perf_counter()
            results = await asyncio.get_event_loop().run_in_executor(
                None, self._ml_predictor.predict_batch, market_df_map,
            )
            elapsed = (__import__("time").perf_counter() - t_start) * 1000
            if results:
                logger.info(
                    f"  ML  : {len(results)}개 코인 | "
                    f"{elapsed:.1f}ms | "
                    f"코인당 {elapsed/len(results):.1f}ms"
                )
                try:
                    from monitoring.dashboard import dashboard_state
                    from datetime import datetime
                    if "ml_predictions" not in dashboard_state.signals:
                        dashboard_state.signals["ml_predictions"] = {}
                    for mkt, res in results.items():
                        dashboard_state.signals["ml_predictions"][mkt] = {
                            "signal":          res.get("signal"),
                            "confidence":      round(res.get("confidence", 0), 4),
                            "buy_prob":        round(res.get("buy_prob",   0), 4),
                            "hold_prob":       round(res.get("hold_prob",  0), 4),
                            "sell_prob":       round(res.get("sell_prob",  0), 4),
                            "model_agreement": round(res.get("model_agreement", 0), 4),
                            "updated_at":      datetime.now().strftime("%H:%M:%S"),
                        }
                    dashboard_state.signals["ml_last_updated"] = (
                        datetime.now().isoformat()
                    )
                    dashboard_state.signals["ml_model_loaded"] = (
                        self._ml_predictor._is_loaded
                    )
                except Exception as _db_e:
                    logger.debug(f" ML   : {_db_e}")
            return results
        except Exception as e:
            logger.warning(f" ML   →   : {e}")
            return {}

    async def _get_ppo_prediction(self, market: str, df) -> Optional[dict]:
        if self._ppo_agent is None or not self._ppo_agent._is_trained:
            return None
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._ppo_agent.predict_from_df(df, market)
            )
        except Exception as e:
            logger.debug(f"PPO   ({market}): {e}")
            return None

    # ── 매수 실행 ────────────────────────────────────────────────
    
    async def _evaluate_entry_signals(self, market: str, df, ml_score: float):
        """(v2.0.4  + v2.1.0  )"""
        try:
            # 1. ATR 변동성 필터 (v2.1.0)
            # 1. ATR 변동성 필터 (v2.1.0) - 자동 계산 추가
            if 'atr' in df.columns and df['atr'].iloc[-1] is not None and df['atr'].iloc[-1] > 0:
                atr = df['atr'].iloc[-1]
            else:
                # ATR 없으면 고가-저가 범위의 2% 추정
                if 'high' in df.columns and 'low' in df.columns:
                    recent_range = (df['high'].iloc[-14:].mean() - df['low'].iloc[-14:].mean())
                    atr = recent_range
                    logger.debug(f"{market} ATR   →  : {atr:.2f}")
                else:
                    atr = df['close'].iloc[-1] * 0.02  # 폴백: 현재가의 2%
                    logger.debug(f"{market} ATR :  2%")
            
            price = df['close'].iloc[-1]
            volatility = (atr / price) * 100 if price > 0 else 0
            
            if volatility < 0.5 or volatility > 5.0:  # 🔧 v2.1.0 완화: 최소값 1.0→0.5 (정상 시장 대응)
                logger.debug(f"{market} ATR  : {volatility:.2f}%")
                logger.debug(f"{market}  : unknown")  # 🔍 TRACE

                logger.debug(f"{market}  : unknown")  # 🔍 TRACE

                return None
            
            # 2. VolumeProfile RR 필터 (v2.1.0)
            # 2. VolumeProfile RR 필터 (v2.1.0)
            try:
                if hasattr(self, 'volume_profile') and hasattr(self.volume_profile, 'calculate'):
                    vp_result = self.volume_profile.calculate(df)
                    vp_rr = vp_result.get('rr', 0) if isinstance(vp_result, dict) else 0
                else:
                    vp_rr = 999  # VolumeProfile 없으면 통과
            except Exception as e:
                logger.debug(f'{market} VolumeProfile  : {e}')
                vp_rr = 999  # 에러 시 통과
            if vp_rr < 0.0:  # disabled: was 0.8, too strict
                logger.debug(f"{market} VolumeProfile RR : {vp_rr:.2f}")
                logger.debug(f"{market}  : unknown")  # 🔍 TRACE

                logger.debug(f"{market}  : unknown")  # 🔍 TRACE

                return None
            
            # 3. Multi-Timeframe Confirmation (v2.1.0)
            if hasattr(self, 'mtf_confirmation'):
                mtf_result = await self.mtf_confirmation.check(market, df)
                if not mtf_result.get('aligned', False):
                    logger.debug(f"{market} MTF ")
                    logger.debug(f"{market}  : unknown")  # 🔍 TRACE

                    logger.debug(f"{market}  : unknown")  # 🔍 TRACE

                    return None
            
            # 4. ML 임계값 확인 (동적, v2.1.0)
            fgi = getattr(self.fear_greed, "index", None) or 50
            buy_threshold = 0.4 if fgi > 30 else 0.3  # FGI<=30: extreme fear, lower threshold
            buy_threshold = 0.4 if fgi > 30 else 0.3  # 🔧 v2.1.0 완화: 0.8→0.4, 0.6→0.3 (실전 데이터 수집)
            
            if ml_score < buy_threshold:
                logger.debug(f"{market} ML  : {ml_score:.3f} < {buy_threshold}")
                logger.debug(f"{market}  : unknown")  # 🔍 TRACE

                logger.debug(f"{market}  : unknown")  # 🔍 TRACE

                return None
            
            #            
            # 6. Kelly Criterion 포지션 크기 (v2.1.0)
            win_rate = getattr(self, 'historical_win_rate', 0.55)
            avg_win = getattr(self, 'avg_win', 0.03)
            avg_loss = getattr(self, 'avg_loss', 0.02)
            
            if avg_loss > 0:
                kelly_fraction = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
                kelly_fraction = max(0.05, min(kelly_fraction, 0.15))  # 5~15% 제한
            else:
                kelly_fraction = 0.10
            
            logger.info(
                f" {market}    | ML: {ml_score:.3f} | "
                f"Kelly: {kelly_fraction:.1%} | ATR: {volatility:.2f}% | RR: {vp_rr:.2f}"
            )
            
            return {
                'action': 'BUY',
                'confidence': ml_score,
                'position_size': kelly_fraction,
                
                'filters_passed': ['ATR', 'VolumeProfile', 'MTF', 'ML', 'Consensus']
            }
            
        except Exception as e:
            logger.error(f"{market}   : {e}")
            logger.debug(f"{market}  : unknown")  # 🔍 TRACE

            logger.debug(f"{market}  : unknown")  # 🔍 TRACE

            return None


    async def _execute_buy(self, market: str, signal: CombinedSignal, df):
        _max_pos = self.settings.trading.max_positions
        if self.portfolio.position_count >= _max_pos:
            logger.info(
                f"   ({market}): "
                f"{self.portfolio.position_count}/{_max_pos} → 매수 취소"
            )
            return
        if self.portfolio.is_position_open(market):
            logger.debug(f"    ({market}): 이미 포지션 존재")
            return
        if market in self._buying_markets:
            logger.debug(f"    ({market}): 매수 진행 중")
            return
        self._buying_markets.add(market)
        # [FIX B] ML=SELL 신호이면 매수 차단
        import time as _time_b
        _ml_pred_b = self._ml_predictions.get(market, {})
        if isinstance(_ml_pred_b, dict):
            _ml_sig_b  = _ml_pred_b.get("signal", "HOLD")
            _ml_conf_b = float(_ml_pred_b.get("confidence", 0))
            if _ml_sig_b == "SELL" and _ml_conf_b >= 0.42:
                logger.warning(
                    f"[ML-BLOCK] {market}: ML=SELL({_ml_conf_b:.2f}) → BUY 차단"
                )
                self._buying_markets.discard(market)
                return
        # [FIX A-2] Sell Cooldown 체크 (10분 재매수 방지)
        if not hasattr(self, "_sell_cooldown"):
            self._sell_cooldown = {}
        _now_b   = _time_b.time()
        _last_sell_b = self._sell_cooldown.get(market, 0)
        if _now_b - _last_sell_b < 600:
            logger.info(
                f"[COOLDOWN] {market}: 매도 후 {int(_now_b - _last_sell_b)}초 경과 → 재매수 대기 (10분)"
            )
            self._buying_markets.discard(market)
            return

        _symbol    = market.replace("KRW-", "")
        _can_buy, _buy_note = self._wallet.can_buy(_symbol)
        if not _can_buy:
            logger.warning(f" SmartWallet  : {_buy_note}")
            self._buying_markets.discard(market)
            return
        logger.info(f" SmartWallet: {_buy_note}")

        krw = await self.adapter.get_balance("KRW")
        can_buy, reason = await self.risk_manager.can_open_position(
            market, krw, self.portfolio.position_count
        )
        if not can_buy:
            logger.info(f"  ({market}): {reason}")
            self._buying_markets.discard(market)
            return

        _is_bear_rev_signal = "BEAR_REVERSAL" in getattr(
            signal, "contributing_strategies", []
        )
        if not _is_bear_rev_signal:
            fg_threshold_adj = self.fear_greed.get_buy_threshold_adjustment()
            if getattr(signal, 'confidence', 0) < (
                self.settings.risk.buy_signal_threshold + fg_threshold_adj
            ):
                logger.debug(
                    f"    ({market}): "
                    f"점수={getattr(signal, 'confidence', 0):.2f} < "
                    f"임계={self.settings.risk.buy_signal_threshold + fg_threshold_adj:.2f} "
                    f"(조정={fg_threshold_adj:+.2f})"
                )
                self._buying_markets.discard(market)
                return

        last = df.iloc[-1]
        try:
            _sl_levels_buy = self.atr_stop.calculate(df, float(last["close"]))
            atr         = _sl_levels_buy.atr
            stop_loss   = _sl_levels_buy.stop_loss
            take_profit = _sl_levels_buy.take_profit
            logger.info(
                f" ATR-SL ({market}): "
                f"SL={stop_loss:,.0f} ({_sl_levels_buy.sl_pct*100:.2f}%) | "
                f"TP={take_profit:,.0f} ({_sl_levels_buy.tp_pct*100:.2f}%) | "
                f"RR={_sl_levels_buy.rr_ratio:.2f} | ATR={atr:,.0f}"
            )
        except Exception as _atr_e:
            logger.warning(
                f" ATR   ({market}): {_atr_e} → 고정비율 사용"
            )
            atr         = float(last["close"]) * 0.02
            stop_loss   = float(last["close"]) * (
                1 - self.settings.risk.atr_stop_multiplier * 0.01
            )
            take_profit = float(last["close"]) * (
                1 + self.settings.risk.atr_target_multiplier * 0.01
            )

        _strategy_name = getattr(signal, "contributing_strategies", ["default"])
        _strategy_name = _strategy_name[0] if _strategy_name else "default"
        _ml_conf       = getattr(signal, "ml_confidence", 0.5)
        position_size  = self.position_sizer.calculate(
            total_capital=krw,
            strategy=_strategy_name,
            market=market,
            confidence=_ml_conf,
        )

        if getattr(signal, "bear_reversal", False):
            position_size *= 0.5
            logger.info(
                f" BEAR_REVERSAL  50%  ({market}): "
                f"₩{position_size*2:,.0f} → ₩{position_size:,.0f}"
            )

        _ml_conf_score  = getattr(signal, "ml_confidence", 0.5)
        _ensemble_score = getattr(signal, "score",         0.5)
        _combined_score = (_ml_conf_score + _ensemble_score) / 2

        if _combined_score >= 0.80:
            _buy_ratio  = 1.0
            _buy_reason = f"강한신호({_combined_score:.2f}) 전량매수"
        elif _combined_score >= 0.60:
            _buy_ratio  = 0.70
            _buy_reason = f"중간신호({_combined_score:.2f}) 70%매수"
        else:
            _buy_ratio  = 0.50
            _buy_reason = f"약한신호({_combined_score:.2f}) 50%매수"

        _original_size = position_size
        position_size  = max(position_size * _buy_ratio, 20_000)
        logger.info(
            f"   ({market}): {_buy_reason} | "
            f"₩{_original_size:,.0f} → ₩{position_size:,.0f}"
        )

        _MIN_POSITION_KRW = 20_000
        _MAX_POSITION_KRW = krw * 0.20

        if position_size < _MIN_POSITION_KRW:
            if krw >= _MIN_POSITION_KRW * 2:
                position_size = _MIN_POSITION_KRW
                logger.info(
                    f"    ({market}): "
                    f"₩{position_size:,.0f} (자본 ₩{krw:,.0f})"
                )
            else:
                logger.debug(
                    f"   ({market}): "
                    f"₩{position_size:,.0f} < 최소 ₩{_MIN_POSITION_KRW:,.0f}"
                )
                self._buying_markets.discard(market)
                return

        if position_size > _MAX_POSITION_KRW:
            position_size = _MAX_POSITION_KRW
            logger.info(
                f"    ({market}): "
                f"₩{position_size:,.0f} (자본의 20%)"
            )

        if position_size < self.settings.trading.min_order_amount:
            logger.debug(
                f"   ({market}): "
                f"₩{position_size:,.0f} < "
                f"최소 ₩{self.settings.trading.min_order_amount:,.0f}"
            )
            self._buying_markets.discard(market)
            return

        if self.portfolio.position_count >= self.settings.trading.max_positions:
            logger.info(
                f"    ({market}): "
                f"{self.portfolio.position_count}/"
                f"{self.settings.trading.max_positions} → 매수 취소"
            )
            self._buying_markets.discard(market)
            return

        current_price    = self._market_prices.get(market, float(last["close"]))
        _buy_raw_volume  = position_size / current_price if current_price > 0 else 0
        _buy_volume      = _floor_vol(market, _buy_raw_volume)
        _adjusted_krw    = _buy_volume * current_price if _buy_volume > 0 else position_size

        req = ExecutionRequest(
            market=market,
            side=OrderSide.BUY,
            amount_krw=_adjusted_krw,
            reason=signal.reasons[0] if getattr(signal, 'reasons', []) else "BUY signal",
            strategy_name=", ".join(getattr(signal, 'contributing_strategies', [])),
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        try:
            result = await self.executor.execute(req)
        finally:
            self._buying_markets.discard(market)

        if result.executed_price > 0:
            self.portfolio.open_position(
                market=market,
                entry_price=result.executed_price,
                volume=result.executed_volume,
                amount_krw=position_size,
                strategy=req.strategy_name,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
            self.trailing_stop.add_position(
                market, result.executed_price, stop_loss, atr
            )

            try:
                if self.ppo_online_trainer is not None:
                    self.ppo_online_trainer.add_experience(
                        market=market, action=1,
                        profit_rate=0.0, hold_hours=0.0,
                    )
            except Exception as _ppo_buy_e:
                logger.debug(f"PPO BUY   : {_ppo_buy_e}")

            if self.position_mgr_v2 is not None:
                try:
                    from risk.position_manager_v2 import PositionV2
                    _pos_v2 = PositionV2(
                        market=market,
                        entry_price=result.executed_price,
                        volume=result.executed_volume,
                        amount_krw=position_size,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        strategy=req.strategy_name,
                    )
                    self.position_mgr_v2.add_position(_pos_v2)
                except Exception as _pv2_e:
                    logger.debug(f"PositionManagerV2  : {_pv2_e}")

            self.partial_exit.add_position(
                market=market,
                entry_price=result.executed_price,
                volume=result.executed_volume,
                take_profit=take_profit,
            )

            _fee_rate = getattr(self.settings.trading, "fee_rate", 0.0005)
            _buy_fee  = position_size * _fee_rate

            log_trade(
                "BUY", market, result.executed_price,
                position_size, req.reason
            )
            await self.telegram.notify_buy(
                market, result.executed_price, position_size,
                req.reason, req.strategy_name
            )

            try:
                await self.db_manager.insert_trade({
                    "timestamp":   datetime.now().isoformat(),
                    "market":      market,
                    "side":        "BUY",
                    "price":       result.executed_price,
                    "volume":      result.executed_volume,
                    "amount_krw":  position_size,
                    "fee":         _buy_fee,
                    "profit_rate": 0.0,
                    "strategy":    req.strategy_name,
                    "reason":      req.reason,
                })
            except Exception as _db_e:
                logger.debug(f"BUY DB  : {_db_e}")

            try:
                await self.db_manager.log_signal({
                    "market":      market,
                    "signal_type": "BUY",
                    "score":       getattr(signal, "score",      0),
                    "confidence":  getattr(signal, "confidence", 0),
                    "strategies":  list(getattr(signal, "contributing_strategies", [])),
                    "regime":      getattr(signal, "regime",     ""),
                    "executed":    True,
                })
            except Exception as _sl_e:
                logger.debug(f"signal_log executed  : {_sl_e}")

        try:
            _exec_price = float(getattr(result, "executed_price",
                          getattr(result, "price", 0)))
            _exec_qty   = float(getattr(result, "executed_volume",
                          getattr(result, "quantity",
                          getattr(result, "qty", 0))))
            if _exec_qty > 0 and _exec_price > 0:
                self._wallet.record_buy(_symbol, _exec_qty, _exec_price)
        except Exception as _we:
            logger.debug(f"SmartWallet record_buy : {_we}")

    # ── 부분 청산 실행 ───────────────────────────────────────────
    async def _execute_partial_sell(
        self, market: str, volume: float, current_price: float
    ):
        pos = self.portfolio.get_position(market)
        if not pos or volume <= 0:
            return

        _order_value   = volume * current_price
        _min_order     = self.settings.trading.min_order_amount
        _pos_total_val = getattr(pos, "volume", 0) * current_price

        if _order_value < _min_order:
            if _pos_total_val >= _min_order:
                logger.info(
                    f"    →   ({market}): "
                    f"부분=₩{_order_value:,.0f} < 최소=₩{_min_order:,.0f} | "
                    f"전체포지션=₩{_pos_total_val:,.0f}"
                )
                await self._execute_sell(
                    market, "소액포지션_전량매도", current_price
                )
            else:
                logger.warning(
                    f"      ({market}): "
                    f"₩{_pos_total_val:,.0f} < ₩{_min_order:,.0f}"
                )
            return

        state           = self.partial_exit.get_state(market)
        executed_levels = (
            sum(1 for lv in state.levels if lv.executed) if state else 0
        )
        reason = f"부분청산_step{executed_levels}"

        req = ExecutionRequest(
            market=market,
            side=OrderSide.SELL,
            amount_krw=0,
            volume=volume,
            reason=reason,
            strategy_name=(
                getattr(self.portfolio.get_position(market), "strategy", "unknown")
                or "unknown"
            ),
        )
        result = await self.executor.execute(req)

        if result.executed_price > 0:
            profit_rate = (
                (result.executed_price - pos.entry_price) / pos.entry_price
            )

            try:
                if self.ppo_online_trainer is not None:
                    import datetime as _dt_ps
                    _entry_time = (
                        getattr(pos, "entry_time",  None)
                        or getattr(pos, "created_at", None)
                    )
                    _hold_hours = 0.0
                    if _entry_time:
                        if isinstance(_entry_time, str):
                            try:
                                _entry_time = _dt_ps.datetime.fromisoformat(_entry_time)
                            except (TypeError, ValueError):
                                _entry_time = _dt_ps.datetime.now()
                        elif isinstance(_entry_time, float):
                            _entry_time = _dt_ps.datetime.fromtimestamp(_entry_time)
                        _hold_hours = (
                            _dt_ps.datetime.now() - _entry_time
                        ).total_seconds() / 3600
                    self.ppo_online_trainer.add_experience(
                        market=market, action=2,
                        profit_rate=profit_rate, hold_hours=_hold_hours,
                    )
            except Exception as _ppo_ps_e:
                logger.debug(f"PPO PARTIAL SELL   : {_ppo_ps_e}")

            pos.volume -= volume
            if pos.volume <= 0:
                self.portfolio.close_position(
                    market, result.executed_price, result.fee, reason
                )
                self.trailing_stop.remove_position(market)
                self.partial_exit.remove_position(market)
            else:
                logger.info(
                    f"    | {market} | "
                    f"={result.executed_price:,.0f} | "
                    f"={volume:.6f} | "
                    f"={profit_rate:.2%} | "
                    f"={pos.volume:.6f}"
                )

            try:
                import datetime as _dt
                _strat = (
                    getattr(
                        self.portfolio.get_position(market), "strategy", "unknown"
                    ) or "unknown"
                )
                _mode = (
                    "paper"
                    if getattr(self.settings, "paper_mode", True)
                    else "live"
                )
                self.db_manager.insert_trade({
                    "market":      market,
                    "side":        "SELL",
                    "price":       result.executed_price,
                    "volume":      volume,
                    "amount_krw":  volume * result.executed_price,
                    "fee":         result.fee,
                    "profit_rate": profit_rate,
                    "strategy":    _strat,
                    "reason":      reason,
                    "mode":        _mode,
                    "timestamp":   _dt.datetime.now().isoformat(),
                })
                logger.debug(f"  DB   ({market}): {reason}")
            except Exception as _db_e:
                logger.debug(f" DB   ({market}): {_db_e}")

            log_trade(
                "PARTIAL_SELL", market, result.executed_price,
                volume * result.executed_price, reason, profit_rate
            )
            await self.telegram.notify_sell(
                market, result.executed_price, volume,
                profit_rate, reason
            )

    # ── 전량 매도 (래퍼) ────────────────────────────────────────
    async def _execute_sell(
        self, market: str, reason: str, current_price: float = None
    ):
        if market in self._selling_markets:
            logger.debug(f"   ({market})")
            return
        self._selling_markets.add(market)
        try:
            await self._execute_sell_inner(market, reason, current_price)
        finally:
            self._selling_markets.discard(market)

    async def _execute_sell_inner(
        self, market: str, reason: str, current_price: float = None
    ):
        _symbol     = market.replace("KRW-", "")
        _confidence = 1.0
        _sell_dec   = self._wallet.get_sell_decision(
            symbol=_symbol, current_price=current_price, confidence=_confidence,
        )

        if getattr(self.settings, "paper_mode", True):
            pos           = self.portfolio._positions.get(market)
            _raw_qty      = float(
                getattr(pos, "volume",
                getattr(pos, "quantity", 0))
            ) if pos else 0.0
            _wallet_sell_qty  = _ceil_vol(market, _raw_qty)
            _wallet_incl_dust = False
        else:
            if not _sell_dec["ok"]:
                logger.warning(
                    f" SmartWallet   ({_symbol}): {_sell_dec['note']}"
                )
                return
            _wallet_sell_qty  = _sell_dec["qty"]
            _wallet_incl_dust = _sell_dec["includes_dust"]
            logger.info(
                f" SmartWallet   | {_symbol} | "
                f"={_wallet_sell_qty:.8f} | {_sell_dec['note']}"
            )

        pos = self.portfolio.get_position(market)
        if not pos:
            return

        req = ExecutionRequest(
            market=market,
            side=OrderSide.SELL,
            amount_krw=0,
            volume=pos.volume,
            reason=reason,
            strategy_name=getattr(pos, "strategy", "unknown") or "unknown",
        )
        result = await self.executor.execute(req)

        if result.executed_price > 0:
            proceeds, profit_rate = self.portfolio.close_position(
                market, result.executed_price, result.fee, reason
            )

            try:
                import asyncio as _asyncio
                import datetime as _dt
                _trade = {
                    "timestamp":   _dt.datetime.now().isoformat(),
                    "market":      market,
                    "side":        "SELL",
                    "price":       result.executed_price,
                    "volume":      result.executed_volume,
                    "amount_krw":  proceeds,
                    "fee":         result.fee if hasattr(result, "fee") else 0.0,
                    # ✅ close_position 반환값은 이미 % 단위 → DB 저장 시 그대로 사용
                    "profit_rate": profit_rate,
                    "strategy":    getattr(pos, "strategy", "unknown"),
                    "reason":      reason,
                    "mode":        "paper",
                }
                if _asyncio.get_event_loop().is_running():
                    _asyncio.ensure_future(
                        self.executor.db_manager.insert_trade(_trade)
                    )
                else:
                    _asyncio.get_event_loop().run_until_complete(
                        self.executor.db_manager.insert_trade(_trade)
                    )
                logger.info(
                    f"[DB-SELL] {market} "
                    f"profit={profit_rate:.2f}%  "
                )
                # [FIX A-2] sell cooldown 기록
                import time as _time_a
                if not hasattr(self, "_sell_cooldown"):
                    self._sell_cooldown = {}
                self._sell_cooldown[market] = _time_a.time()
                logger.debug(f"[COOLDOWN-SET] {market}: 매도 시각 기록 완료")
                self._save_cooldown_to_db()  # [FIX1] DB 저장
            except Exception as _e:
                logger.warning(f"[DB-SELL]  : {_e}")

            try:
                if self.ppo_online_trainer is not None:
                    import datetime as _ppo_dt
                    _pos_ref = pos
                    _etime   = (
                        getattr(_pos_ref, "entry_time",  None)
                        or getattr(_pos_ref, "created_at", None)
                    )
                    _hold_h  = 0.0
                    if _etime:
                        if isinstance(_etime, str):
                            try:
                                _etime = _ppo_dt.datetime.fromisoformat(_etime)
                            except (TypeError, ValueError):
                                _etime = _ppo_dt.datetime.now()
                        elif isinstance(_etime, (int, float)):
                            try:
                                _etime = _ppo_dt.datetime.fromtimestamp(_etime)
                            except (TypeError, OSError):
                                _etime = _ppo_dt.datetime.now()
                        elif not isinstance(_etime, _ppo_dt.datetime):
                            _etime = _ppo_dt.datetime.now()
                        _hold_h = (
                            _ppo_dt.datetime.now() - _etime
                        ).total_seconds() / 3600
                    _pnl = profit_rate / 100
                    self.ppo_online_trainer.add_experience(
                        market=market, action=2,
                        profit_rate=_pnl, hold_hours=_hold_h,
                    )
                    _buf = self.ppo_online_trainer.get_buffer_stats()
                    logger.info(
                        f" PPO   ({market}): "
                        f"PnL={_pnl*100:.2f}% | 보유={_hold_h:.1f}h | "
                        f"버퍼={_buf.get('size',0)}/{_buf.get('max',1000)}"
                    )
            except Exception as _ppo_e:
                logger.debug(f"PPO SELL   : {_ppo_e}")

            self.trailing_stop.remove_position(market)
            self.partial_exit.remove_position(market)

            if "손절" in reason or "stop" in reason.lower() or "트레일링" in reason or "ATR" in reason:
                if not hasattr(self, "_sl_cooldown"):
                    self._sl_cooldown = {}
                import datetime as _dt
                self._sl_cooldown[market] = (
                    _dt.datetime.now() + _dt.timedelta(hours=4)
                )
                logger.info(
                    f"    ({market}): 4시간 재매수 금지"
                )
                _cd_until = (
                    _dt.datetime.now() + _dt.timedelta(hours=4)
                ).isoformat()
                await self.db_manager.set_state(
                    f"sl_cooldown_{market}", _cd_until
                )

            self.risk_manager.record_trade_result(profit_rate > 0)
            log_trade(
                "SELL", market, result.executed_price,
                proceeds, reason, profit_rate
            )
            await self.telegram.notify_sell(
                market, result.executed_price, result.executed_volume,
                profit_rate, reason
            )

        try:
            _sold_qty = float(getattr(result, "executed_volume",
                        getattr(result, "quantity",
                        getattr(result, "qty", _wallet_sell_qty))))
            if _sold_qty > 0:
                self._wallet.record_sell(
                    symbol=_symbol, sold_qty=_sold_qty,
                    includes_dust=_wallet_incl_dust,
                )
        except Exception as _we:
            logger.debug(f"SmartWallet record_sell : {_we}")
    # ── 초기화 헬퍼 ─────────────────────────────────────────────
    def _apply_walk_forward_params(self):
        try:
            from backtesting.walk_forward import WalkForwardRunner
            params = WalkForwardRunner.load_optimized_params()
            if not params:
                logger.info("Walk-Forward   →  ")
                return
            applied = 0
            for strategy_name, info in params.items():
                if strategy_name not in self._strategies:
                    continue
                strategy  = self._strategies[strategy_name]
                is_active = info.get("is_active", True)
                if not is_active:
                    strategy.disable()
                    logger.info(
                        f"   {strategy_name}  "
                        f"(OOS ={info.get('oos_sharpe', 0):.3f})"
                    )
                else:
                    if info.get("params"):
                        strategy.params.update(info["params"])
                    weight_boost = info.get("weight_boost", 1.0)
                    if weight_boost != 1.0:
                        old_weight = self.signal_combiner.STRATEGY_WEIGHTS.get(
                            strategy_name, 1.0
                        )
                        new_weight = old_weight * weight_boost
                        self.signal_combiner.STRATEGY_WEIGHTS[strategy_name] = new_weight
                        logger.info(
                            f"   {strategy_name}  "
                            f"{old_weight:.1f} → {new_weight:.1f} "
                            f"(boost={weight_boost}x)"
                        )
                    applied += 1
            logger.success(f"✅ Walk-Forward 파라미터 적용: {applied}개 전략")
        except Exception as e:
            logger.warning(f"Walk-Forward    ( ): {e}")

    def _load_strategies(self):
        from strategies.momentum.macd_cross import MACDCrossStrategy
        from strategies.momentum.rsi_divergence import RSIDivergenceStrategy
        from strategies.momentum.supertrend import SupertrendStrategy
        from strategies.mean_reversion.bollinger_squeeze import BollingerSqueezeStrategy
        from strategies.mean_reversion.vwap_reversion import VWAPReversionStrategy
        from strategies.volatility.vol_breakout import VolBreakoutStrategy
        from strategies.volatility.atr_channel import ATRChannelStrategy
        from strategies.market_structure.order_block import OrderBlockStrategy

        strategies = [
            MACDCrossStrategy(), RSIDivergenceStrategy(), SupertrendStrategy(),
            BollingerSqueezeStrategy(), VWAPReversionStrategy(),
            VolBreakoutStrategy(), ATRChannelStrategy(), OrderBlockStrategy(),
        ]
        for s in strategies:
            self._strategies[s.NAME] = s
        logger.info(f" {len(self._strategies)}개 전략 로드 완료")

    async def _load_ml_model(self):
        try:
            from models.inference.predictor import MLPredictor
            self._ml_predictor = MLPredictor()
            ok = await asyncio.get_event_loop().run_in_executor(
                None, self._ml_predictor.load_model
            )
            if ok and self._device == "cuda" and self._ml_predictor._model is not None:
                self._ml_predictor._model = maybe_compile(
                    self._ml_predictor._model,
                    backend="eager",
                    mode="default",
                )
            log_gpu_status()
            logger.success("✅ ML 앙상블 모델 로드 완료")
        except Exception as e:
            logger.warning(f"ML    (   ): {e}")

    # ── 마켓 스캐너 ──────────────────────────────────────────────
    async def _market_scanner(self) -> list:
        cfg = self._SCANNER_CONFIG
        now = time.time()
        if now - self._last_scan_time < cfg["interval_sec"]:
            return []
        self._last_scan_time = now

        try:
            all_markets = await self._get_all_krw_markets()
            if not all_markets:
                return []

            fixed_markets = set(self.markets) if hasattr(self, "markets") else set()
            exclude       = set(cfg["exclude_markets"]) | fixed_markets
            scan_targets  = [m for m in all_markets if m not in exclude]
            logger.debug(f"[Scanner]  {len(scan_targets)}개 종목 스캔 시작")

            surge_candidates = []
            batch_size       = 20
            for i in range(0, len(scan_targets), batch_size):
                batch   = scan_targets[i:i + batch_size]
                tasks   = [self._check_surge(m, cfg) for m in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for market, result in zip(batch, results):
                    if isinstance(result, Exception):
                        continue
                    if result and result.get("is_surge"):
                        surge_candidates.append(result)
                await asyncio.sleep(0.5)

            surge_candidates.sort(
                key=lambda x: x.get("score", 0), reverse=True
            )

            new_markets     = []
            current_dynamic = set(self._dynamic_markets)

            for candidate in surge_candidates[: cfg["max_dynamic_coins"]]:
                market = candidate["market"]
                if market not in current_dynamic:
                    self._dynamic_markets.append(market)
                    new_markets.append(market)
                    logger.info(
                        f" [Scanner]  : {market} | "
                        f" ={candidate['vol_ratio']:.1f}x | "
                        f"={candidate['price_change']:.2%} | "
                        f"={candidate['trade_amount']:,.0f}"
                    )

            if len(self._dynamic_markets) > cfg["max_dynamic_coins"]:
                self._dynamic_markets = self._dynamic_markets[
                    -cfg["max_dynamic_coins"]:
                ]

            if new_markets:
                logger.info(
                    f"[Scanner]    {len(new_markets)}개 감시 추가: "
                    f"{new_markets} | 동적풀 총 {len(self._dynamic_markets)}개"
                )
            else:
                logger.debug(
                    f"[Scanner]     | "
                    f" : {len(self._dynamic_markets)}개"
                )
            return new_markets

        except Exception as e:
            logger.warning(f"[Scanner]  : {e}")
            return []

    async def _get_all_krw_markets(self) -> list:
        try:
            import aiohttp
            url = "https://api.upbit.com/v1/market/all"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, params={"isDetails": "false"}
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
            return [
                item["market"]
                for item in data
                if item["market"].startswith("KRW-")
            ]
        except Exception as e:
            logger.warning(f"[Scanner]    : {e}")
            return []

    async def _check_surge(self, market: str, cfg: dict) -> dict:
        try:
            df = await self.rest_collector.get_ohlcv(market, "minute1", 25)
            if df is None or len(df) < 10:
                return {"is_surge": False}

            recent_vol    = float(df["volume"].iloc[-1])
            recent_price  = float(df["close"].iloc[-1])
            recent_amount = recent_vol * recent_price

            if recent_amount < cfg["min_trade_amount"]:
                return {"is_surge": False}

            avg_vol = float(df["volume"].iloc[-21:-1].mean())
            if avg_vol <= 0:
                return {"is_surge": False}

            vol_ratio     = recent_vol / avg_vol
            price_5m_ago  = float(df["close"].iloc[-6])
            price_change  = (recent_price - price_5m_ago) / price_5m_ago

            is_surge = (
                vol_ratio    >= cfg["vol_surge_ratio"]
                and price_change >= cfg["price_change_min"]
            )
            if not is_surge:
                return {"is_surge": False}

            score = vol_ratio * (price_change * 100)
            return {
                "is_surge":     True,
                "market":       market,
                "vol_ratio":    vol_ratio,
                "price_change": price_change,
                "trade_amount": recent_amount,
                "score":        score,
                "price":        recent_price,
            }
        except Exception:
            return {"is_surge": False}

    async def _get_active_markets(self) -> list:
        fixed   = list(self.markets) if hasattr(self, "markets") else []
        dynamic = [m for m in self._dynamic_markets if m not in fixed]
        return fixed + dynamic

    # ── 백테스트 ─────────────────────────────────────────────────
    async def _run_backtest_v2(
        self,
        market: str,
        interval: str = "minute60",
        count: int    = 500,
        initial_capital: float = 1_000_000.0,
    ) -> dict:
        import numpy as np
        import pandas as pd

        FEE_RATE      = 0.0005
        SLIPPAGE_RATE = 0.0003

        try:
            df = await self.rest_collector.get_ohlcv(market, interval, count)
            if df is None or len(df) < 50:
                return {"error": "데이터 부족"}

            df        = df.reset_index(drop=True)
            capital   = initial_capital
            position  = 0.0
            entry_price = stop_loss = take_profit = 0.0
            trades    = []
            equity_curve = [capital]

            df["ema20"] = df["close"].ewm(span=20).mean()
            df["ema50"] = df["close"].ewm(span=50).mean()
            delta       = df["close"].diff()
            gain        = delta.clip(lower=0).rolling(14).mean()
            loss        = (-delta.clip(upper=0)).rolling(14).mean()
            df["rsi"]   = 100 - 100 / (1 + gain / (loss + 1e-9))
            ema12       = df["close"].ewm(span=12).mean()
            ema26       = df["close"].ewm(span=26).mean()
            df["macd"]      = ema12 - ema26
            df["macd_sig"]  = df["macd"].ewm(span=9).mean()
            close_prev = df["close"].shift(1)
            tr = pd.concat([
                df["high"] - df["low"],
                (df["high"] - close_prev).abs(),
                (df["low"]  - close_prev).abs(),
            ], axis=1).max(axis=1)
            df["atr"] = tr.rolling(14).mean()

            from risk.stop_loss.atr_stop import _get_profile_by_price
            _entry_est = float(df["close"].iloc[-1]) if len(df) > 0 else 1000
            _p         = _get_profile_by_price(_entry_est)
            profile    = {"atr_low": _p["min_sl"], "atr_high": _p["max_sl"]}

            for i in range(50, len(df)):
                row   = df.iloc[i]
                close = float(row["close"])
                atr   = float(row["atr"]) if not pd.isna(row["atr"]) else close * 0.02
                atr_pct = atr / close

                if position > 0:
                    equity_curve.append(capital + position * (close - entry_price))
                    if close <= stop_loss:
                        exit_price = stop_loss * (1 - SLIPPAGE_RATE)
                        pnl  = (exit_price - entry_price) * position
                        pnl -= (entry_price + exit_price) * position * FEE_RATE
                        capital += position * entry_price + pnl
                        trades.append({
                            "type": "LOSS", "pnl": pnl,
                            "pnl_pct": pnl / (position * entry_price),
                            "entry": entry_price, "exit": exit_price,
                        })
                        position = 0.0
                        continue
                    if close >= take_profit:
                        exit_price = take_profit * (1 - SLIPPAGE_RATE)
                        pnl  = (exit_price - entry_price) * position
                        pnl -= (entry_price + exit_price) * position * FEE_RATE
                        capital += position * entry_price + pnl
                        trades.append({
                            "type": "WIN", "pnl": pnl,
                            "pnl_pct": pnl / (position * entry_price),
                            "entry": entry_price, "exit": exit_price,
                        })
                        position = 0.0
                        continue

                if position == 0:
                    ema20    = float(row["ema20"])
                    ema50    = float(row["ema50"])
                    rsi      = float(row["rsi"])
                    macd     = float(row["macd"])
                    macd_sig = float(row["macd_sig"])
                    long_signals = sum([
                        ema20 > ema50,
                        50 < rsi < 70,
                        macd > macd_sig,
                        close > ema20,
                    ])
                    if long_signals >= 3:
                        if trades:
                            wins   = [t for t in trades if t["type"] == "WIN"]
                            losses = [t for t in trades if t["type"] == "LOSS"]
                            wr     = len(wins) / len(trades)
                            avg_w  = sum(t["pnl_pct"] for t in wins) / max(len(wins), 1)
                            avg_l  = abs(
                                sum(t["pnl_pct"] for t in losses)
                                / max(len(losses), 1)
                            )
                            rr     = avg_w / max(avg_l, 1e-9)
                            b      = max(0.5, rr)
                            p      = max(0.1, min(0.9, wr))
                            q      = 1 - p
                            kelly  = max(0.03, min(0.20, ((p * b - q) / b) * 0.5))
                        else:
                            kelly = 0.05

                        invest_amount = capital * kelly
                        entry_price   = close * (1 + SLIPPAGE_RATE)
                        position      = invest_amount / entry_price
                        capital      -= invest_amount

                        atr_low  = profile["atr_low"]
                        atr_high = profile["atr_high"]
                        if atr_pct < atr_low:
                            sl_mult, tp_mult = 1.5, 3.0
                        elif atr_pct < atr_high:
                            sl_mult, tp_mult = 2.0, 4.0
                        else:
                            sl_mult, tp_mult = 2.5, 5.0

                        stop_loss   = entry_price - atr * sl_mult
                        take_profit = entry_price + atr * tp_mult

            if position > 0:
                exit_price = float(df["close"].iloc[-1]) * (1 - SLIPPAGE_RATE)
                pnl  = (exit_price - entry_price) * position
                pnl -= (entry_price + exit_price) * position * FEE_RATE
                t    = "WIN" if pnl > 0 else "LOSS"
                trades.append({
                    "type": t, "pnl": pnl,
                    "pnl_pct": pnl / (position * entry_price),
                    "entry": entry_price, "exit": exit_price,
                })
                capital += position * entry_price + pnl

            if not trades:
                return {"market": market, "total_trades": 0, "error": "거래 없음"}

            total_trades  = len(trades)
            wins          = [t for t in trades if t["type"] == "WIN"]
            losses        = [t for t in trades if t["type"] == "LOSS"]
            win_rate      = len(wins) / total_trades
            total_profit  = sum(t["pnl"] for t in wins)
            total_loss    = abs(sum(t["pnl"] for t in losses))
            profit_factor = total_profit / max(total_loss, 1e-9)
            total_return  = (capital - initial_capital) / initial_capital
            returns       = [t["pnl_pct"] for t in trades]
            sharpe        = (
                (np.mean(returns) / (np.std(returns) + 1e-9)) * (252 ** 0.5)
                if len(returns) > 1 else 0.0
            )
            equity_arr = np.array(equity_curve)
            peak       = np.maximum.accumulate(equity_arr)
            drawdown   = (equity_arr - peak) / (peak + 1e-9)
            mdd        = float(drawdown.min())

            result = {
                "market":        market,
                "total_trades":  total_trades,
                "win_rate":      round(win_rate,      4),
                "profit_factor": round(profit_factor, 4),
                "total_return":  round(total_return,  4),
                "sharpe":        round(sharpe,        4),
                "mdd":           round(mdd,           4),
                "final_capital": round(capital,       0),
                "wins":          len(wins),
                "losses":        len(losses),
            }
            logger.info(
                f"[Backtest v2] {market} | "
                f"={total_trades} WR={win_rate:.1%} "
                f"PF={profit_factor:.2f} Return={total_return:.2%} "
                f"Sharpe={sharpe:.2f} MDD={mdd:.2%}"
            )
            return result

        except Exception as e:
            logger.warning(f"[Backtest v2] {market} : {e}")
            return {"market": market, "error": str(e)}

    async def _run_backtest_all(self) -> dict:
        markets = list(self.markets) if hasattr(self, "markets") else []
        if not markets:
            return {}
        logger.info(f"[Backtest v2]    | {len(markets)}개 코인")
        tasks  = [self._run_backtest_v2(m) for m in markets]
        raw    = await asyncio.gather(*tasks, return_exceptions=True)
        results = {}
        lines   = ["[백테스트 v2 결과]"]
        for market, result in zip(markets, raw):
            if isinstance(result, Exception):
                results[market] = {"error": str(result)}
            else:
                results[market] = result
                if "error" not in result:
                    lines.append(
                        f"{market}: WR={round(result['win_rate']*100,1)}% "
                        f"PF={result['profit_factor']} "
                        f"Ret={round(result['total_return']*100,1)}% "
                        f"MDD={round(result['mdd']*100,1)}%"
                    )
        try:
            await self.telegram.send_message(" | ".join(lines))
        except Exception:
            pass
        logger.info(f"[Backtest v2]  | {len(results)}개 결과")
        return results

    # ── PPO 초기화 / 훈련 ────────────────────────────────────────
    async def _init_ppo_agent(self):
        try:
            from models.rl.ppo_agent import PPOTradingAgent, check_ppo_dependencies
            deps = check_ppo_dependencies()
            if not all(deps.values()):
                missing = [k for k, v in deps.items() if not v]
                logger.info(
                    f" PPO    (: {missing})"
                )
                return
            self._ppo_agent = PPOTradingAgent(use_gpu=(self._device == "cuda"))
            loaded = self._ppo_agent.load_model()
            if loaded:
                logger.success("✅ PPO 모델 로드 완료 (저장된 비중 사용)")
            else:
                logger.info(" PPO   —       ")
                from datetime import datetime, timedelta
                self.scheduler.add_job(
                    self._auto_train_ppo, "date",
                    run_date=datetime.now() + timedelta(minutes=10),
                    id="ppo_initial_train",
                )
                logger.info(" PPO  :   10   ")
        except Exception as e:
            logger.warning(f"PPO   ( ): {e}")

    async def _auto_train_ppo(
        self, total_timesteps: int = 200_000, notify: bool = True
    ):
        logger.info(" PPO    —   ...")
        if notify:
            await self.telegram.send_message(
                f"🤖 PPO 강화학습 훈련 시작\n"
                f"  대상 코인: "
                f"{', '.join(self.settings.trading.target_markets)}\n"
                f"  에피소드: {total_timesteps:,}스텝\n"
                f"  완료 시 텔레그램 알림 (약 15분 소요)"
            )
        try:
            from models.rl.ppo_agent import PPOTradingAgent
            from data.processors.candle_processor import CandleProcessor
            import pandas as pd

            markets   = self.settings.trading.target_markets
            processor = CandleProcessor()

            logger.info("     ...")
            raw_dfs = []
            for m in markets:
                try:
                    df = await self.rest_collector.get_ohlcv(m, "minute60", 500)
                    raw_dfs.append(df)
                except Exception as e:
                    raw_dfs.append(e)
                await asyncio.sleep(0.35)

            processed_dfs = []
            for i, df in enumerate(raw_dfs):
                if isinstance(df, Exception) or df is None:
                    continue
                try:
                    p = await processor.process(markets[i], df, "60")
                    if p is not None and len(p) > 100:
                        processed_dfs.append(p)
                except Exception:
                    pass

            if not processed_dfs:
                logger.warning("PPO    —  ")
                return

            combined_df = pd.concat(processed_dfs, ignore_index=True)
            logger.info(
                f"   : {len(combined_df)}샘플 "
                f"({len(processed_dfs)}개 코인)"
            )

            agent  = PPOTradingAgent(use_gpu=(self._device == "cuda"))
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: agent.train(
                    combined_df, total_timesteps=total_timesteps
                ),
            )

            if "error" not in result:
                self._ppo_agent = agent
                logger.success(
                    f"✅ PPO 자동 훈련 완료 | "
                    f"PnL={result.get('pnl_pct',0):+.2f}% | "
                    f"승률={result.get('win_rate',0):.1f}% | "
                    f"샤프={result.get('sharpe',0):.3f}"
                )
                if notify:
                    await self.telegram.send_message(
                        f"✅ PPO 훈련 완료\n"
                        f"  PnL  : {result.get('pnl_pct',0):+.2f}%\n"
                        f"  승률 : {result.get('win_rate',0):.1f}%\n"
                        f"  샤프 : {result.get('sharpe',0):.3f}\n"
                        f"  모델 : models/saved/ppo/ 저장됨\n"
                        f"  다음 재훈련: 매주 월요일 03:00"
                    )
                self.scheduler.add_job(
                    lambda: asyncio.create_task(
                        self._auto_train_ppo(total_timesteps)
                    ),
                    "cron",
                    day_of_week="mon", hour=3, minute=0,
                    id="ppo_weekly_retrain",
                    replace_existing=True,
                )
            else:
                logger.warning(f"PPO  : {result.get('error')}")

        except Exception as e:
            logger.error(f"PPO   : {e}")

    # ── 포지션 복원 ──────────────────────────────────────────────
    async def _restore_positions_from_db(self):
        try:
            import aiosqlite as _aio
            async with _aio.connect(str(self.db_manager.db_path)) as db:
                db.row_factory = _aio.Row
                cur = await db.execute("""
                    SELECT b.market, b.price, b.volume, b.amount_krw,
                           b.strategy, b.timestamp
                    FROM trade_history b
                    LEFT JOIN trade_history s
                        ON b.market = s.market
                       AND s.side   = 'SELL'
                       AND s.timestamp > b.timestamp
                    WHERE b.side = 'BUY'
                      AND b.mode = 'paper'
                      AND s.id IS NULL
                    ORDER BY b.timestamp ASC
                """)
                rows = await cur.fetchall()

            restored = 0
            total_invested = 0.0
            for row in rows:
                try:
                    mkt         = row["market"]
                    _price      = float(row["price"]      or 0)
                    _volume     = float(row["volume"]     or 0)
                    _amount_krw = float(row["amount_krw"] or 0)
                    _strategy   = row["strategy"] or "unknown"

                    if self.portfolio.is_position_open(mkt):
                        continue
                    if _price <= 0 or _volume <= 0:
                        logger.warning(
                            f"   ({mkt}): 가격/수량 없음"
                        )
                        continue

                    self.portfolio.open_position(
                        market=mkt,
                        entry_price=_price,
                        volume=_volume,
                        amount_krw=_amount_krw,
                        strategy=_strategy,
                        stop_loss=_price * 0.97,
                        take_profit=_price * 1.05,
                    )
                    self.trailing_stop.add_position(
                        market=mkt,
                        entry_price=_price,
                        initial_stop=_price * 0.97,
                        atr=0.0,
                    )

                    if self.position_mgr_v2 is not None:
                        try:
                            from risk.position_manager_v2 import PositionV2
                            _pv2 = PositionV2(
                                market=mkt,
                                entry_price=_price,
                                volume=_volume,
                                amount_krw=_amount_krw,
                                stop_loss=_price * 0.97,
                                take_profit=_price * 1.05,
                                strategy=_strategy,
                            )
                            self.position_mgr_v2.add_position(_pv2)
                        except Exception as _rv2_e:
                            logger.debug(f"M4  : {_rv2_e}")

                    self.partial_exit.add_position(
                        market=mkt,
                        entry_price=_price,
                        volume=_volume,
                        take_profit=_price * 1.05,
                    )
                    self.adapter._paper_balance["KRW"] = max(
                        0.0,
                        self.adapter._paper_balance.get("KRW", 1_000_000) - _amount_krw,
                    )
                    coin = mkt.replace("KRW-", "")
                    self.adapter._paper_balance[coin] = (
                        self.adapter._paper_balance.get(coin, 0.0) + _volume
                    )
                    restored       += 1
                    total_invested += _amount_krw
                    logger.info(
                        f"   | {mkt} | "
                        f"={_price:,.0f} | "
                        f"=₩{_amount_krw:,.0f} | {_strategy}"
                    )

                    try:
                        _exited = await self.db_manager.get_partial_exit_ratio(mkt)
                        if _exited and _exited > 0:
                            self.partial_exit.restore_executed_levels(mkt, _exited)
                            logger.info(
                                f"   | {mkt} | ={_exited:.0%}"
                            )
                    except Exception as _pe_e:
                        logger.debug(f"   ({mkt}): {_pe_e}")

                except Exception as _row_e:
                    logger.warning(
                        f"   "
                        f"({row['market'] if row else '?'}): {_row_e}"
                    )
                    continue

            if restored:
                logger.info(
                    f"   : {restored} | "
                    f"=₩{total_invested:,.0f}"
                )
                try:
                    _krw_cash = await self.adapter.get_balance("KRW")
                    _open_pos = {
                        m: {"volume": pos.volume}
                        for m, pos in self.portfolio.open_positions.items()
                    }
                    self.adapter.sync_paper_balance(_krw_cash, _open_pos)
                except Exception as _sync_e:
                    logger.debug(f"   : {_sync_e}")
            else:
                logger.info("    ( )")

            try:
                from datetime import datetime as _dt_cls
                _today_str      = _dt_cls.now().strftime("%Y-%m-%d")
                _bear_count_key = f"_bear_rev_count_{_today_str}"
                _bear_today     = 0
                try:
                    import aiosqlite as _aio2
                    async with _aio2.connect(
                        str(self.db_manager.db_path)
                    ) as _db2:
                        async with _db2.execute("""
                            SELECT COUNT(*) FROM trade_history
                            WHERE strategy LIKE '%BEAR_REVERSAL%'
                              AND side = 'BUY'
                              AND DATE(timestamp) = DATE('now','localtime')
                        """) as _cur2:
                            _row2 = await _cur2.fetchone()
                            _bear_today = (
                                int(_row2[0])
                                if _row2 and _row2[0] is not None
                                else 0
                            )
                except Exception:
                    _bear_today = 0
                setattr(self, _bear_count_key, _bear_today)
                _remain = max(0, 6 - _bear_today)
                _status = (
                    "⛔ 오늘 한도 초과"
                    if _bear_today >= 6
                    else f"잔여 {_remain}회"
                )
                logger.info(
                    f"  BEAR_REVERSAL  : "
                    f" {_bear_today} → {_status}"
                )
            except Exception as _br_e:
                logger.warning(f" BEAR_REVERSAL   : {_br_e}")

        except Exception as e:
            import traceback
            logger.warning(f"    (): {e}")
            logger.debug(traceback.format_exc())

    async def _restore_sl_cooldown(self):
        try:
            if not hasattr(self, "_sl_cooldown"):
                self._sl_cooldown = {}
            import datetime as _dt_cd
            if self.db_manager._conn is not None:
                async with self.db_manager._lock:
                    async with self.db_manager._conn.execute(
                        "SELECT key, value FROM bot_state "
                        "WHERE key LIKE 'sl_cooldown_%'"
                    ) as _cur:
                        _rows = await _cur.fetchall()
                restored_count = 0
                now = _dt_cd.datetime.now()
                for _key, _val in _rows:
                    try:
                        _until = _dt_cd.datetime.fromisoformat(_val)
                        if _until > now:
                            _mkt = _key.replace("sl_cooldown_", "", 1)
                            self._sl_cooldown[_mkt] = _until
                            _rem = int(
                                (_until - now).total_seconds() // 60
                            )
                            logger.info(
                                f"   ({_mkt}): {_rem}분 남음"
                            )
                            restored_count += 1
                        else:
                            await self.db_manager.delete_state(_key)
                    except Exception as _e:
                        logger.debug(f"   [{_key}]: {_e}")
                if restored_count:
                    logger.info(
                        f"    : {restored_count} "
                    )
                else:
                    logger.info("    ")
        except Exception as _e:
            logger.warning(f"    (): {_e}")

    async def _save_initial_candles(self):
        markets = self.settings.trading.target_markets
        saved   = 0
        for market in markets:
            try:
                df = await self.rest_collector.get_ohlcv(
                    market, interval="minute60", count=200
                )
                if df is not None and len(df) > 0:
                    self.cache_manager.set_ohlcv(market, "1h", df)
                    saved += 1
                    logger.debug(f"   | {market} | {len(df)}개")
            except Exception as e:
                logger.debug(f"   ({market}): {e}")
        logger.info(
            f"   NpyCache   | "
            f"{saved}/{len(markets)}개 코인"
        )

    async def _initial_data_fetch(self):
        logger.info("    ...")
        markets = self.settings.trading.target_markets
        tasks   = [
            self.rest_collector.get_ohlcv(m, "minute60", 200)
            for m in markets
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success = sum(
            1 for r in results
            if r is not None and not isinstance(r, Exception)
        )
        await self._save_initial_candles()
        logger.info(
            f"     ({success}/{len(markets)}개 성공)"
        )
        try:
            raw_balances = await self.adapter.get_balances()
            if isinstance(raw_balances, list) and raw_balances:
                self._wallet.scan_balances(raw_balances)
            self._wallet.print_status()
        except Exception as e:
            logger.warning(f"SmartWallet   : {e}")

    # ── 스케줄된 작업 ────────────────────────────────────────────
    async def _run_auto_retrain(self):
        try:
            logger.info("[AutoTrainer]     ...")
            result = await self.auto_trainer.run_if_needed()
            if result:
                await self._load_ml_model()
                logger.info("[AutoTrainer]    +   ")
            else:
                logger.info("[AutoTrainer]      ")
        except Exception as e:
            logger.error(f"[AutoTrainer] : {e}")

    def _register_schedules(self):
        from datetime import datetime, timedelta

        self.scheduler.add_job(
            self._scheduled_price_update,
            "interval", seconds=60, id="price_update",
        )
        self.scheduler.add_job(
            self._scheduled_daily_data,
            "interval", hours=1, id="daily_data",
        )
        self.scheduler.add_job(
            self._scheduled_daily_report,
            "cron", hour=0, minute=0, id="daily_report",
        )
        self.scheduler.add_job(
            self._scheduled_model_retrain,
            "interval",
            hours=self.settings.ml.retrain_interval_hours,
            id="retrain",
        )
        first_run = datetime.now() + timedelta(hours=24)
        self.scheduler.add_job(
            self._scheduled_paper_report,
            "interval", hours=24, id="paper_report",
            next_run_time=first_run,
        )
        self.scheduler.add_job(
            self._scheduled_kimchi_update,
            "interval", hours=6, id="kimchi_update",
        )
        self.scheduler.add_job(
            self._scheduled_fear_greed_update,
            "interval", hours=1, id="fear_greed_update",
        )
        self.scheduler.add_job(
            self._scheduled_walk_forward,
            "cron", day_of_week="mon", hour=2, minute=0,
            id="walk_forward",
        )
        self.scheduler.add_job(
            self._scheduled_news_update,
            "interval", minutes=30, id="news_update",
        )
        self.scheduler.add_job(
            self._scheduled_position_summary,
            "interval", hours=1, id="position_summary",
        )
        self.scheduler.add_job(
            self._scheduled_performance_check,
            "interval", hours=1, id="performance_check",
        )
        from pathlib import Path
        if not Path("config/optimized_params.json").exists():
            self.scheduler.add_job(
                self._scheduled_walk_forward, "date",
                run_date=datetime.now() + timedelta(minutes=30),
                id="walk_forward_initial",
            )
            logger.info(
                " Walk-Forward  : 30   "
                "(config/optimized_params.json )"
            )
        self.scheduler.add_job(
            lambda: __import__(
                "utils.gpu_utils", fromlist=["warmup_keep_alive"]
            ).warmup_keep_alive(),
            "interval", minutes=5, id="cuda_keepalive",
        )
        self.scheduler.add_job(
            self.telegram.send_hourly_summary,
            "interval", hours=1,
            id="hourly_telegram_summary",
            misfire_grace_time=60,
        )
        self.scheduler.add_job(
            self._scheduled_ppo_online_retrain,
            "cron", day_of_week="sun", hour=4, minute=0,
            id="ppo_online_retrain",
        )
        logger.info(
            f"    "
            f"({len(self.scheduler.get_jobs())}개 작업)"
        )

    async def _scheduled_position_summary(self):
        try:
            from datetime import datetime
            from monitoring.dashboard import dashboard_state
            positions = list(self.portfolio._positions.values())
            if not positions:
                return
            now   = datetime.now()
            lines = [
                "📊 <b>APEX BOT 포지션 현황</b>",
                f"🕐 {now.strftime('%m/%d %H:%M')} KST\n",
            ]
            total_invested = total_eval = total_pnl_krw = 0.0
            win_count = 0
            for pos in positions:
                market   = getattr(pos, "market",      "?")
                entry    = float(getattr(pos, "entry_price", 0) or 0)
                qty      = float(getattr(pos, "quantity",    0) or 0)
                current  = float(
                    self.cache_manager.get_current_price(market) or entry
                )
                invested = entry * qty
                eval_val = current * qty
                pnl_pct  = (current - entry) / entry * 100 if entry else 0
                pnl_krw  = eval_val - invested
                total_invested += invested
                total_eval     += eval_val
                total_pnl_krw  += pnl_krw
                if pnl_pct >= 0:
                    win_count += 1
                entry_time = getattr(pos, "entry_time", None)
                try:
                    hold_h   = (
                        (now - entry_time).total_seconds() / 3600
                        if entry_time else 0
                    )
                    hold_str = f"{hold_h:.1f}h"
                except Exception:
                    hold_str = "?"
                sl_pct   = float(getattr(pos, "stop_loss_pct",  -3.0) or -3.0)
                tp_pct   = float(getattr(pos, "take_profit_pct", 5.0) or  5.0)
                sl_dist  = sl_pct - pnl_pct
                tp_dist  = tp_pct - pnl_pct
                ml_info  = (
                    dashboard_state.signals
                    .get("ml_predictions", {})
                    .get(market, {})
                )
                ml_sig   = ml_info.get("signal",     "-")
                ml_conf  = float(ml_info.get("confidence", 0))
                ml_icon  = {
                    "BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"
                }.get(ml_sig, "⚪")
                coin     = market.replace("KRW-", "")
                pnl_icon = "🟢" if pnl_pct >= 0 else "🔴"
                lines.append(
                    f"{pnl_icon} <b>{coin}</b>  "
                    f"{pnl_pct:+.2f}% ({pnl_krw:+,.0f}원)"
                )
                lines.append(
                    f"   진입 {entry:,.0f} → 현재 {current:,.0f}  "
                    f"보유 {hold_str}"
                )
                lines.append(
                    f"   SL까지 {sl_dist:+.1f}%  TP까지 {tp_dist:+.1f}%"
                )
                lines.append(
                    f"   ML {ml_icon}{ml_sig}({ml_conf:.0%})  "
                    f"수량 {qty:.4f}\n"
                )
            total_pnl_pct = (
                (total_eval - total_invested) / total_invested * 100
                if total_invested else 0
            )
            cash         = float(getattr(self.portfolio, "cash", 0) or 0)
            total_assets = total_eval + cash
            lines.append("─────────────────────")
            lines.append(f"💰 총 평가금액: <b>{total_assets:,.0f}원</b>")
            lines.append(
                f"📈 포지션 손익: <b>{total_pnl_pct:+.2f}%</b>  "
                f"({total_pnl_krw:+,.0f}원)"
            )
            lines.append(f"💵 현금 잔고:   {cash:,.0f}원")
            lines.append(f"🏆 수익 포지션: {win_count}/{len(positions)}개")
            fg = getattr(self, "_fear_greed_index", None)
            if fg is not None:
                fg_label = (
                    "극단적 공포" if fg < 25 else
                    "공포"       if fg < 45 else
                    "중립"       if fg < 55 else
                    "탐욕"       if fg < 75 else
                    "극단적 탐욕"
                )
                lines.append(f"\n😨 공포탐욕: {fg}  ({fg_label})")
            btc_status = self.correlation_filter.get_btc_status()
            if btc_status.get("trend") == "DOWN":
                lines.append("⚠️ BTC 하락세 감지 - 신규 매수 차단 중")
            news_sig = dashboard_state.signals.get("news_sentiment", {})
            if news_sig.get("overall_sentiment") in ("BEARISH", "VERY_BEARISH"):
                lines.append(
                    f"📰 뉴스 감성: "
                    f"{news_sig.get('overall_sentiment')} ⚠️"
                )
            await self.telegram.send_message("\n".join(lines))
        except Exception as e:
            logger.debug(f"   : {e}")

    async def _scheduled_performance_check(self):
        try:
            trades  = await self.db_manager.get_trades(limit=50)
            if not trades:
                return
            await self.performance_tracker.update(trades)
            metrics = self.performance_tracker.get_metrics()
            score   = await self.live_readiness.check(self.performance_tracker)
            logger.info(
                f" : win_rate={metrics.get('win_rate',0):.1%} "
                f"sharpe={metrics.get('sharpe_ratio',0):.2f} "
                f"mdd={metrics.get('max_drawdown',0):.1%} "
                f"live_score={score:.0f}/100"
            )
            if score >= 70:
                logger.info("LiveReadiness 70  - Live   ")
            elif score < 30 and len(trades) > 20:
                await self.telegram.send_alert(
                    "WARNING",
                    f"LiveReadiness 점수 {score:.0f}/100 - 전략 점검 필요",
                )
        except Exception as e:
            logger.debug(f"  : {e}")

    async def _scheduled_price_update(self):
        pass  # ws_collector 실시간 처리

    async def _scheduled_daily_data(self):
        for market in self.settings.trading.target_markets:
            try:
                df = await self.rest_collector.get_ohlcv(market, "day", 200)
                if df is not None:
                    await self.candle_processor.process(market, df, "1440")
            except Exception as e:
                logger.error(f"   ({market}): {e}")

    async def _scheduled_daily_report(self):
        stats     = self.portfolio.get_statistics()
        krw       = await self.adapter.get_balance("KRW")
        total     = self.portfolio.get_total_value(krw)
        daily_pnl = self.portfolio.get_daily_pnl(total)
        report    = {
            **stats,
            "date":           now_kst().strftime("%Y-%m-%d"),
            "daily_pnl":      daily_pnl,
            "total_assets":   total,
            "open_positions": self.portfolio.position_count,
        }
        await self.telegram.notify_daily_report(report)
        try:
            await self.db_manager.save_daily_performance({
                "date":           report.get("date"),
                "total_assets":   report.get("total_assets",   0),
                "daily_pnl":      report.get("daily_pnl",      0),
                "open_positions": report.get("open_positions",  0),
                "win_rate":       report.get("win_rate",        0),
                "trade_count":    report.get("trade_count",     0),
            })
            logger.info(" daily_performance DB  ")
        except Exception as _dpe:
            logger.debug(f"daily_performance  : {_dpe}")

    async def _scheduled_model_retrain(self):
        if self._ml_predictor:
            logger.info(" ML   ...")
            try:
                from datetime import datetime
                await self.db_manager.save_model_metrics({
                    "timestamp":  datetime.now().isoformat(),
                    "model_name": "ensemble",
                    "val_acc":    getattr(
                        self._ml_predictor, "_last_val_acc",   0.0
                    ),
                    "train_loss": getattr(
                        self._ml_predictor, "_last_train_loss", 0.0
                    ),
                    "val_loss":   getattr(
                        self._ml_predictor, "_last_val_loss",  0.0
                    ),
                    "parameters": 12299965,
                })
                logger.info(" model_metrics DB  ")
            except Exception as _mme:
                logger.debug(f"model_metrics  : {_mme}")
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._ml_predictor.retrain
                )
                logger.info(" ML   ")
            except Exception as e:
                logger.error(f" : {e}")

    async def _scheduled_ppo_online_retrain(self):
        try:
            if not hasattr(self, "ppo_online_trainer"):
                return
            stats = self.ppo_online_trainer.get_buffer_stats()
            logger.info(
                f"[PPOOnline]    | "
                f"buffer={stats.get('count',0)}개 | "
                f"avg_profit={stats.get('avg_profit',0):.2%} | "
                f"win_rate={stats.get('win_rate',0):.1%}"
            )
            result = await self.ppo_online_trainer.train_if_ready()
            if result:
                await self._init_ppo_agent()
                await self.telegram.send_message(
                    f"🤖 PPO 온라인 재학습 완료\n"
                    f"경험 {stats.get('count',0)}건 학습\n"
                    f"평균 수익률: {stats.get('avg_profit',0):.2%}\n"
                    f"승률: {stats.get('win_rate',0):.1%}"
                )
                logger.info("[PPOOnline]     +  ")
            else:
                logger.info("[PPOOnline]     (   )")
        except Exception as e:
            logger.error(f"[PPOOnline]  : {e}")

    async def _scheduled_paper_report(self, hours: int = 24):
        logger.info(f" {hours}     ...")
        try:
            data = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: generate_paper_report(
                    hours=hours, output_dir="reports/paper",
                ),
            )
            m       = data.get("metrics", {})
            pnl     = m.get("total_pnl_pct", 0)
            sign    = "+" if pnl >= 0 else ""
            fg_line = ""
            if self.fear_greed.is_valid:
                fg_line = (
                    f"공포탐욕: {self.fear_greed.index} "
                    f"({self.fear_greed.label})\n"
                )
            btc_status = self.correlation_filter.get_btc_status()
            btc_line   = ""
            if btc_status.get("is_globally_blocked"):
                btc_line = (
                    f"⚠️ BTC 급락 차단 중 "
                    f"({btc_status['block_remaining_sec']}초 남음)\n"
                )
            msg = (
                f"📊 [{hours}시간 리포트]\n"
                f"수익률 : {sign}{pnl:.2f}%\n"
                f"승률   : {m.get('win_rate', 0):.1f}%\n"
                f"거래수 : {m.get('total_trades', 0)}회\n"
                f"샤프   : {m.get('sharpe_ratio', 0):.3f}\n"
                f"최대DD : -{m.get('max_drawdown_pct', 0):.2f}%\n"
                f"{fg_line}{btc_line}"
                f"리포트 : reports/paper/ 폴더 확인"
            )
            await self.telegram.send_message(msg)
            logger.success("✅ 페이퍼 리포트 생성 완료")
        except Exception as e:
            logger.error(f"   : {e}")

    async def _scheduled_kimchi_update(self):
        try:
            await self.kimchi_monitor.fetch_all()
            summary = self.kimchi_monitor.get_summary()
            try:
                from monitoring.dashboard import dashboard_state
                premium_val = (
                    summary.get("premium_pct")
                    if isinstance(summary, dict)
                    else None
                )
                if premium_val is None and hasattr(
                    self.kimchi_monitor, "premium_pct"
                ):
                    premium_val = self.kimchi_monitor.premium_pct
                dashboard_state.signals["kimchi_premium"] = premium_val
            except Exception:
                pass
            logger.info(f"   : {summary}")
        except Exception as e:
            logger.warning(f"   : {e}")

    async def _scheduled_fear_greed_update(self):
        try:
            ok = await self.fear_greed.fetch()
            if ok:
                logger.info(
                    f"   : {self.fear_greed.index} "
                    f"({self.fear_greed.label})"
                )
                idx = self.fear_greed.index or 50
                if idx <= 15:
                    await self.telegram.send_message(
                        f"⚠️ 공포탐욕: 극도 공포 {idx} "
                        f"— 역발상 매수 기회 탐색 중"
                    )
                elif idx >= 85:
                    await self.telegram.send_message(
                        f"⚠️ 공포탐욕: 극도 탐욕 {idx} "
                        f"— 신규 매수 억제 모드"
                    )
        except Exception as e:
            logger.warning(f"   : {e}")

    async def _scheduled_walk_forward(self):
        logger.info("  Walk-Forward  ...")
        try:
            from backtesting.walk_forward import run_weekly_walk_forward
            results    = await run_weekly_walk_forward()
            profitable = [k for k, v in results.items() if v.is_profitable]
            msg = (
                f"🔬 Walk-Forward 완료\n"
                f"수익 전략: "
                f"{', '.join(profitable) if profitable else '없음'}\n"
                f"최적 파라미터 → config/optimized_params.json 저장"
            )
            await self.telegram.send_message(msg)
        except Exception as e:
            logger.error(f"Walk-Forward  : {e}")

    async def _scheduled_news_update(self):
        try:
            count = await self.news_analyzer.fetch_news()
            logger.debug(f"  : {count}")
        except Exception as e:
            logger.debug(f"  : {e}")

    async def _ws_reconnect_loop(self):
        RECONNECT_DELAY = 5
        MAX_DELAY       = 60
        delay = RECONNECT_DELAY
        while True:
            try:
                if self.ws_collector and not self.ws_collector.is_connected():
                    logger.warning(
                        f" WebSocket   → {delay}   "
                    )
                    
                    # ===== 시그널 평가 및 진입 로직 (v2.1.0) =====
                    try:
                        for market in self.target_markets:
                            try:
                                # 데이터 가져오기
                                df = self.data_manager.get_market_data(market) if hasattr(self, 'data_manager') else None
                                if df is None or len(df) == 0:
                                    continue
                                
                                # ML 점수 가져오기 (캐시 또는 새로 계산)
                                ml_score = 0
                                if hasattr(self, 'ml_predictor') and self.ml_predictor:
                                    try:
                                        prediction = await self.ml_predictor.predict(market, df)
                                        ml_score = prediction.get('score', 0) if prediction else 0
                                    except Exception as e:
                                        logger.debug(f"{market} ML  : {e}")
                                        continue
                                
                                # 시그널 평가
                                if ml_score > 0.1:  # 최소 임계값
                                    signal = await self._evaluate_entry_signals(market, df, ml_score)
                                    if signal and signal.get('action') == 'BUY':
                                        logger.info(f" {market}   ")
                                        await self._execute_buy(market, signal, df)
                            
                            except Exception as e:
                                logger.error(f"{market}   : {e}")
                    
                    except Exception as e:
                        logger.error(f"   : {e}")
                    # =================================================

                    await asyncio.sleep(delay)
                    await self.ws_collector.reconnect()
                    logger.info(" WebSocket  ")
                    delay = RECONNECT_DELAY
                else:
                    delay = RECONNECT_DELAY
                    await asyncio.sleep(10)
            except Exception as e:
                logger.error(f" WebSocket  : {e}")
                delay = min(delay * 2, MAX_DELAY)
                await asyncio.sleep(delay)

    # ── 대시보드 상태 업데이트 ───────────────────────────────────
    async def _update_dashboard_state(self, krw: float, total_value: float):
        from monitoring.dashboard import dashboard_state
        try:
            stats     = self.portfolio.get_statistics()
            daily_pnl = self.portfolio.get_daily_pnl(total_value)
            drawdown  = self.portfolio.get_current_drawdown(total_value)

            try:
                _ks = self.kimchi_monitor.get_summary()
                _kv = (
                    _ks.get("premium_pct")
                    if isinstance(_ks, dict)
                    else getattr(self.kimchi_monitor, "premium_pct", None)
                )
                if _kv is not None:
                    dashboard_state.signals["kimchi_premium"] = _kv
                _ns = self.news_analyzer.get_dashboard_summary()
                _gs = _ns.get("global_sentiment", None)
                if _gs is not None:
                    _nl = (
                        "Positive" if _gs >= 0.2 else
                        "Negative" if _gs <= -0.2 else
                        "Neutral"
                    )
                    dashboard_state.signals["news_sentiment"] = _nl
                    dashboard_state.signals["news_score"]     = round(float(_gs), 3)
                _fg = getattr(self.fear_greed, "index", None) or 50
                if _fg <= 25:
                    _regime = "BEAR"
                elif _fg >= 75:
                    _regime = "BULL"
                elif _fg <= 45:
                    _regime = "BEAR_WATCH"
                else:
                    _regime = "NEUTRAL"
                dashboard_state.signals["market_regime"] = _regime
            except Exception:
                pass

            _pos_dict = {}
            for _m, _pos in self.portfolio.open_positions.items():
                _cp  = getattr(_pos, "current_price", None) or _pos.entry_price
                _pnl = (_cp - _pos.entry_price) / _pos.entry_price * 100
                _pos_dict[_m] = {
                    "entry_price":        _pos.entry_price,
                    "current_price":      _cp,
                    "volume":             _pos.volume,
                    "unrealized_pnl_pct": round(_pnl, 2),
                    "hold_hours":         0.0,
                    "strategy":           getattr(_pos, "strategy", "-"),
                }
            dashboard_state.portfolio.update({
                "total_krw":   round(total_value, 2),
                "krw_balance": round(krw, 2),
                "positions":   _pos_dict,
                "pnl_today":   round(daily_pnl, 4),
                "type":        "portfolio",
            })

            positions_detail = []
            for market, pos in self.portfolio.open_positions.items():
                cur_price = getattr(pos, "current_price", None) or pos.entry_price
                invested  = round(pos.entry_price * pos.volume, 0)
                positions_detail.append({
                    "market":        market,
                    "strategy":      getattr(pos, "strategy", "-"),
                    "entry_price":   pos.entry_price,
                    "current_price": cur_price,
                    "amount_krw":    invested,
                    "profit_rate":   round(pos.unrealized_pnl_pct / 100, 4),
                    "take_profit":   getattr(pos, "take_profit", None),
                    "stop_loss":     getattr(pos, "stop_loss",   None),
                })
            invested_total = sum(p["amount_krw"] for p in positions_detail)

            dashboard_state.portfolio.update({
                "total_assets":     round(total_value, 0),
                "cash":             round(krw, 0),
                "invested":         round(invested_total, 0),
                "positions":        len(positions_detail),
                "positions_detail": positions_detail,
                "mode":             (
                    "PAPER"
                    if getattr(self, "mode", "paper") == "paper"
                    else "LIVE"
                ),
                "pnl": round(daily_pnl, 0),
            })

            dashboard_state.metrics.update({
                "daily_pnl":      daily_pnl,
                "total_trades":   stats.get("total_trades",  0),
                "win_rate":       stats.get("win_rate",       0),
                "profit_factor":  stats.get("profit_factor",  0),
                "max_drawdown":   drawdown,
                "sharpe_ratio":   stats.get("sharpe_ratio",   0),
                "strategy_stats": stats.get("strategy_stats", []),
            })

            kimchi_pct = None
            try:
                premiums = self.kimchi_monitor.get_all_premiums()
                if premiums:
                    vals       = [v for v in premiums.values() if v is not None]
                    kimchi_pct = round(sum(vals) / len(vals), 2) if vals else None
            except Exception:
                pass

            bear_count = getattr(self, "_bear_reversal_today", 0)
            btc_status = self.correlation_filter.get_btc_status()

            news_label = "--"
            try:
                ns    = self.news_analyzer.get_dashboard_summary()
                score = ns.get("global_sentiment", 0.0)
                news_label = (
                    "긍정적" if score >  0.3 else
                    "부정적" if score < -0.3 else
                    "중립"
                )
            except Exception:
                pass

            last_regime = "--"
            try:
                last_regime = getattr(self, "_last_regime", "--")
            except Exception:
                pass

            dashboard_state.signals.update({
                "fear_greed":          self.fear_greed.index,
                "fear_greed_label":    self.fear_greed.label,
                "kimchi_premium":      kimchi_pct,
                "news_sentiment":      news_label,
                "market_regime":       last_regime,
                "bear_reversal_count": bear_count,
                "btc_shock_blocked":   btc_status.get("is_globally_blocked", False),
            })

        except Exception as _e:
            logger.debug(f"  : {_e}")


    def _load_cooldown_from_db(self) -> dict:
        """DB bot_state 테이블에서 sell cooldown 복원."""
        import json, sqlite3 as _sq
        result: dict = {}
        try:
            db_file = "database/apex_bot.db"
            conn = _sq.connect(db_file)
            cur  = conn.cursor()
            cur.execute("SELECT value FROM bot_state WHERE key='sell_cooldown' LIMIT 1")
            row = cur.fetchone()
            conn.close()
            if row:
                raw = json.loads(row[0])
                result = {k: datetime.fromisoformat(v) for k, v in raw.items()}
                print(f"  [COOLDOWN-RESTORE] {len(result)}개 복원")
        except Exception as e:
            print(f"  [COOLDOWN-RESTORE ERR] {e}")
        return result

    def _save_cooldown_to_db(self):
        """sell cooldown 데이터를 DB bot_state에 저장."""
        import json, sqlite3 as _sq
        try:
            db_file = "database/apex_bot.db"
            data = {k: v.isoformat() for k, v in self._sell_cooldown.items()
                    if isinstance(v, datetime)}
            conn = _sq.connect(db_file)
            cur  = conn.cursor()
            cur.execute("""
                INSERT INTO bot_state(key, value, updated_at)
                VALUES('sell_cooldown', ?, datetime('now','localtime'))
                ON CONFLICT(key) DO UPDATE
                SET value=excluded.value, updated_at=excluded.updated_at
            """, (json.dumps(data),))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"  [COOLDOWN-SAVE ERR] {e}")

    def _get_hold_hours(self, market: str) -> float:
        """포지션 보유 시간(시간)을 반환."""
        try:
            pos = self._portfolio.get(market) or {}
            entry_time = pos.get("entry_time") or pos.get("timestamp")
            if entry_time is None:
                return 0.0
            if isinstance(entry_time, str):
                from datetime import datetime as _dt
                entry_time = _dt.fromisoformat(entry_time)
            elif isinstance(entry_time, (int, float)):
                from datetime import datetime as _dt
                ts = entry_time / 1000 if entry_time > 1e10 else entry_time
                entry_time = _dt.fromtimestamp(ts)
            from datetime import datetime as _dt2
            return (_dt2.now() - entry_time).total_seconds() / 3600
        except Exception:
            return 0.0

    def _time_based_tp_threshold(self, market: str) -> float:
        """보유 시간별 익절 기준 반환.
        0-6h  : +1.5%
        6-24h : +0.8%
        >24h  : +0.3%
        """
        h = self._get_hold_hours(market)
        if h < 6:
            return 1.5
        elif h < 24:
            return 0.8
        return 0.3