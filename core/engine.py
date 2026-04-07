"""
APEX BOT - 메인 트레이딩 엔진  v2.0.0
이벤트 루프 중심 비동기 아키텍처

신규 통합 모듈:
  ✅ 트레일링 스탑 실시간 연동 (TrailingStopManager)
  ✅ 부분 청산 (PartialExitManager) - 3단계 자동 익절
  ✅ 상관관계 필터 (CorrelationFilter) - BTC 급락 시 알트 차단
  ✅ 김치 프리미엄 모니터링 (KimchiPremiumMonitor)
  ✅ 공포탐욕 지수 연동 (FearGreedMonitor)
  ✅ 거래량 스파이크 감지 (VolumeSpikeDetector)
  ✅ GPU 가속 (RTX 50xx CUDA 지원)
  ✅ 24h 페이퍼 리포트 자동 생성
"""
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
from signals.filters.trend_filter  import TrendFilter
from signals.filters.volume_profile import VolumeProfileAnalyzer
from data.storage.db_manager import DatabaseManager
from data.storage.cache_manager import CacheManager
from execution.upbit_adapter import UpbitAdapter
from execution.executor import OrderExecutor, ExecutionRequest, OrderSide
from risk.risk_manager import RiskManager
from risk.position_sizer import KellyPositionSizer
from risk.stop_loss.trailing_stop import TrailingStopManager
from risk.stop_loss.atr_stop import ATRStopLoss, StopLevels
from risk.partial_exit import PartialExitManager          # ✅ 부분 청산
from signals.signal_combiner    import SignalCombiner, CombinedSignal
# ── Layer 3 모듈 ──────────────────────────────────────────
try:
    from execution.live_guard           import LiveGuard, LiveGuardConfig
    LIVE_GUARD_OK = True
except ImportError:
    LIVE_GUARD_OK = False
try:
    from signals.mtf_signal_merger      import MTFSignalMerger, TFDirection
    MTF_MERGER_OK = True
except ImportError:
    MTF_MERGER_OK = False
try:
    from risk.position_manager_v2       import PositionManagerV2, PositionV2, ExitReason
    POS_MGR_V2_OK = True
except ImportError:
    POS_MGR_V2_OK = False
try:
    from monitoring.analytics.strategy_analyzer import StrategyAnalyzer
    STRATEGY_ANALYZER_OK = True
except ImportError:
    STRATEGY_ANALYZER_OK = False
try:
    from monitoring.analytics.live_readiness    import LiveReadinessChecker
    LIVE_READINESS_OK = True
except ImportError:
    LIVE_READINESS_OK = False
from signals.filters.regime_detector import RegimeDetector, MarketRegime
from signals.filters.correlation_filter import CorrelationFilter  # ✅ 상관관계 필터
from signals.filters.kimchi_premium import KimchiPremiumMonitor   # ✅ 김치 프리미엄
from signals.filters.fear_greed import FearGreedMonitor           # ✅ 공포탐욕 지수
from signals.filters.volume_spike import VolumeSpikeDetector      # ✅ 거래량 스파이크
from signals.filters.news_sentiment import NewsSentimentAnalyzer
from signals.filters.orderbook_signal import OrderbookSignalAnalyzer  # ✅ Fix   # ✅ 뉴스 감성 분석
from strategies.base_strategy import SignalType
from monitoring.dashboard import DashboardServer, update_dashboard
from models.train.auto_trainer         import AutoTrainer
from models.train.ppo_online_trainer   import PPOOnlineTrainer
from monitoring.performance_tracker    import PerformanceTracker
from monitoring.telegram_bot import TelegramNotifier
from utils.logger import setup_logger, log_trade, log_signal, log_risk
from utils.helpers import now_kst, Timer
from utils.gpu_utils import setup_gpu, maybe_compile, log_gpu_status, clear_gpu_cache
from utils.cpu_optimizer import (  # ✅ Fix: CPU 최적화
    create_strategy_pool, create_io_thread_pool,
    pin_main_thread_to_pcores, optimize_asyncio_event_loop,
    log_cpu_status,
)
from monitoring.paper_report import generate_paper_report

class TradingEngine:
    """
    APEX BOT 메인 엔진 v2.0.0

    아키텍처:
    - P코어 0-1: 이벤트 루프 + 주문 실행 (최고 우선순위)
    - P코어 2-3: 전략 병렬 처리 (ProcessPoolExecutor)
    - P코어 4-5: ML 전처리
    - E코어: 데이터 수집 + 모니터링

    신규 기능:
    - 트레일링 스탑: 3% 활성화 → 1.5% 추적 → 래칫 보호
    - 부분 청산: 목표가 50/100/150% 단계별 25/50/25% 익절
    - 상관관계 필터: BTC -3% 이상 급락 시 알트 신규 매수 차단
    - 김치 프리미엄: 프리미엄 >3% 시 매수 억제
    - 공포탐욕 지수: Extreme Fear(<20) 매수 강화, Extreme Greed(>80) 매수 억제
    - 거래량 스파이크: 평균 대비 3x 이상 거래량 → 신호 보정
    """

    VERSION = "2.0.0"

    def __init__(self):
        self.settings = get_settings()

        # 핵심 컴포넌트 초기화
        self.state_machine = StateMachine()
        self.event_bus = EventBus()
        self.portfolio = PortfolioManager()
        self.regime_detector = RegimeDetector()
        self.signal_combiner = SignalCombiner(self.settings)

        # 데이터 레이어
        self.ws_collector = None  # lazy init in start()
        self.rest_collector = RestCollector()
        self.candle_processor = CandleProcessor()
        self.db_manager = DatabaseManager()
        self.cache_manager = CacheManager()

        # 실행 레이어
        self.adapter = UpbitAdapter()
        self.executor = OrderExecutor(self.adapter)
        self.risk_manager = RiskManager()
        self.position_sizer = KellyPositionSizer()

        # ✅ 트레일링 스탑 (실시간 연동)
        self.trailing_stop = TrailingStopManager()

        # ── Layer 2: 추가 모듈 초기화 ────────────────────────────
        self.atr_stop           = ATRStopLoss()
        self.mtf_processor      = MTFProcessor()
        self.trend_filter       = TrendFilter()
        self.volume_profile    = VolumeProfileAnalyzer()
        self.auto_trainer       = AutoTrainer()
        self.perf_tracker       = PerformanceTracker()

        # ✅ 부분 청산 관리자
        self.partial_exit = PartialExitManager()

        # ── Layer 3: M2 LiveGuard ────────────────────────
        self.live_guard = LiveGuard() if LIVE_GUARD_OK else None
        if self.live_guard:
            logger.info("✅ LiveGuard (M2) 초기화")

        # ── Layer 3: M3 MTFSignalMerger ─────────────────
        self.mtf_merger = MTFSignalMerger() if MTF_MERGER_OK else None
        if self.mtf_merger:
            logger.info("✅ MTFSignalMerger (M3) 초기화")

        # ── Layer 3: M4 PositionManagerV2 ───────────────
        self.position_mgr_v2 = PositionManagerV2(
            max_hold_hours     = 72,
            breakeven_trigger  = 0.02,
            partial_exit_1     = 0.03,
            partial_exit_1_pct = 0.30,
            partial_exit_2     = 0.05,
            partial_exit_2_pct = 0.30,
            pyramid_max        = 2,
            pyramid_trigger    = 0.02,
        ) if POS_MGR_V2_OK else None
        if self.position_mgr_v2:
            logger.info("✅ PositionManagerV2 (M4) 초기화")

        # ── Layer 3: M7 분석기 ───────────────────────────
        self.strategy_analyzer  = StrategyAnalyzer()      if STRATEGY_ANALYZER_OK  else None
        self.live_readiness     = LiveReadinessChecker()  if LIVE_READINESS_OK     else None
        if self.strategy_analyzer:
            logger.info("✅ StrategyAnalyzer (M7) 초기화")

        # ✅ 상관관계 필터
        self.correlation_filter = CorrelationFilter()

        # ✅ 김치 프리미엄 모니터
        self.kimchi_monitor = KimchiPremiumMonitor()

        # ✅ 공포탐욕 지수 모니터
        self.fear_greed = FearGreedMonitor()

        # ✅ 거래량 스파이크 감지기
        self.volume_spike = VolumeSpikeDetector()

        # ✅ OrderBook 분석기 초기화
        try:
            from data.processors.orderbook_analyzer import OrderBookAnalyzer
            self.orderbook_analyzer = OrderBookAnalyzer()
            logger.info('✅ OrderBookAnalyzer 초기화 완료')
        except Exception as _ob_err:
            self.orderbook_analyzer = None
            import traceback
            logger.error(f'❌ OrderBookAnalyzer 초기화 실패: {_ob_err}')
            logger.error(traceback.format_exc())

        # ✅ OrderBlockDetector 초기화
        try:
            from strategies.order_block_detector import OrderBlockDetector
            self.ob_detector = OrderBlockDetector(impulse_mult=2.0, lookback=100)
            logger.info('✅ OrderBlockDetector 초기화 완료')
        except Exception as _obd_err:
            self.ob_detector = None
            logger.warning(f'⚠️ OrderBlockDetector 초기화 실패: {_obd_err}')

        # ✅ RateLimitManager 초기화
        try:
            from core.rate_limit_manager import RateLimitManager
            self.rate_limiter = RateLimitManager()
        except Exception as _rl_err:
            self.rate_limiter = None
            logger.warning(f'⚠️ RateLimitManager 초기화 실패: {_rl_err}')

        # ✅ SlippageModel 초기화
        try:
            from core.slippage_model import SlippageModel
            self.slippage_model = SlippageModel()
        except Exception as _sm_err:
            self.slippage_model = None
            logger.warning(f'⚠️ SlippageModel 초기화 실패: {_sm_err}')

        # ✅ 뉴스 감성 분석기
        self.news_analyzer = NewsSentimentAnalyzer(use_finbert=True)

        # 모니터링
        self.dashboard = DashboardServer()
        self.telegram = TelegramNotifier()

        # 스케줄러
        self.scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
        # ✅ Step 2: Ultra 5 225F P코어 4개 활용 (기존 2 → 4)
        self._process_pool = create_strategy_pool()  # P코어 2-3

        # 전략 레지스트리
        self._strategies = {}
        self._ml_predictor = None
        self._ppo_agent = None   # ✅ FIX: AttributeError 방지

        # 내부 상태
        self._market_prices: Dict[str, float] = {}
        self._last_signal_time: Dict[str, float] = {}
        self._signal_cooldown = 300  # 신호 재발생 최소 5분
        self._device = "cpu"
        self._buying_markets: set = set()
        self._selling_markets: set = set()  # 이중 매도 방지 락
        self._ml_batch_cache: dict = {}   # GPU 배치 추론 결과 캐시

        self._wallet = SmartWalletManager()
        # ✅ FIX: _SCANNER_CONFIG 초기화
        self._SCANNER_CONFIG = {
            "interval_sec":     30,        # 30초마다 전체 마켓 스캔
            "vol_surge_ratio":   3.0,      # 거래량 3배 이상 급증
            "price_change_min":  0.02,     # 가격 2% 이상 변동
            "min_trade_amount":  50_000_000,  # 최소 거래대금 5천만원
            "max_dynamic_coins": 20,       # 동적 감시 최대 20개
            "exclude_markets":   [],       # 제외 마켓 없음
        }
        self._selling_markets: set = set()
        self._dynamic_markets: list = []   # 동적 발굴 코인 풀
        self._last_scan_time: float = 0.0  # 마지막 스캔 시각
        self.markets: list = []            # 전체 분석 대상 (고정+동적)
        self.markets = self.settings.trading.target_markets
        logger.info(f"⚡ APEX BOT v{self.VERSION} 초기화 완료")

    # ── 시작 / 종료 ───────────────────────────────────────────────
    async def start(self):
        """봇 시작 (전체 초기화 + 메인 루프)"""
        setup_logger(
            self.settings.monitoring.log_level,
            self.settings.monitoring.log_dir,
        )
        logger.info("=" * 60)
        logger.info(f"  APEX BOT v{self.VERSION} 시작")
        logger.info(f"  모드: {self.settings.mode.upper()}")
        logger.info(f"  대상: {len(self.settings.trading.target_markets)}개 코인")
        logger.info("=" * 60)

        try:
            # 0. 상태 전환: IDLE -> INITIALIZING
            self.state_machine.transition(BotState.INITIALIZING)

            # 1. 데이터베이스 초기화
            await self.db_manager.initialize()
            self.executor.db_manager = self.db_manager  # DB 저장 연결

            # 2. 업비트 API 연결
            await self.adapter.initialize()
            krw_balance = await self.adapter.get_balance("KRW")
            self.portfolio.set_initial_capital(krw_balance)
            logger.info(f"💰 초기 자본: ₩{krw_balance:,.0f}")

            # ✅ DB에서 미청산 포지션 복원
            await self._restore_positions_from_db()

            # 3-A. 손절 쿨다운 복원 (재시작 후에도 유지)
            await self._restore_sl_cooldown()

            # 3. 전략 로드
            self._load_strategies()

            # 4. GPU 초기화 (CUDA benchmark + TF32 + RTX50xx 감지)
            self._device = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: setup_gpu(
                    use_gpu=self.settings.ml.use_gpu,
                    benchmark=True,
                    tf32=True,
                )
            )

            # 5. ML 모델 로드 (GPU)
            await self._load_ml_model()

            # 5b. PPO 강화학습 에이전트 자동 로드/훈련
            await self._init_ppo_agent()

            # 6. 외부 데이터 사전 로드 (김치 프리미엄, 공포탐욕, 뉴스)
            await self._init_external_data()

            # 7. 대시보드 시작
            self.dashboard.setup(engine_ref=self)
            await self.dashboard.start()

            # 8. 텔레그램 봇 시작
            await self.telegram.initialize(engine_ref=self)

            # 9. 스케줄러 등록
            self._register_schedules()

            # ── Layer 2: AutoTrainer 일일 재학습 (새벽 3시) ──
            self.scheduler.add_job(
                self._run_auto_retrain,
                'cron',
                hour=3,
                minute=0,
                id='auto_retrain',
                replace_existing=True,
            )
            self.scheduler.add_job(
                self._run_backtest_all,
                'cron',
                hour=3,
                minute=0,
                id='backtest_v2_daily',
                replace_existing=True,
            )
            self.scheduler.start()
    
            # 10. 상태 머신 → RUNNING
            self.state_machine.transition(BotState.RUNNING)
            await update_dashboard({"type": "status", "status": "RUNNING"})

            # 11. WebSocket 수집기 초기화
            async def _on_ws_message(data):
                msg_type = data.get('ty', data.get('type', ''))
                market   = data.get('cd', data.get('code', ''))

                # ── ticker: 현재가 업데이트 ──────────────────────────────────
                if msg_type == 'ticker':
                    price = data.get('tp', data.get('trade_price', 0))
                    if market and price:
                        self._market_prices[market] = price
                        self.correlation_filter.update_price(market, price)
                        self.kimchi_monitor.update_upbit_price(market, price)

                # ── orderbook: 호가창 캐시 저장 ─────────────────────────────
                elif msg_type == 'orderbook':
                    if market:
                        # SIMPLE 포맷 → orderbook_units 변환
                        raw_units = data.get('obu', data.get('orderbook_units', []))
                        normalized = {
                            "market": market,
                            "timestamp": data.get('tms', 0),
                            "total_ask_size": data.get('tas', 0.0),
                            "total_bid_size": data.get('tbs', 0.0),
                            "orderbook_units": [
                                {
                                    "ask_price": u.get('ap', u.get('ask_price', 0)),
                                    "bid_price": u.get('bp', u.get('bid_price', 0)),
                                    "ask_size":  u.get('as', u.get('ask_size',  0)),
                                    "bid_size":  u.get('bs', u.get('bid_size',  0)),
                                }
                                for u in raw_units
                            ],
                        }
                        self.cache_manager.set_orderbook(market, normalized)

            self.ws_collector = WebSocketCollector(
                markets=self.settings.trading.target_markets,
                on_message=_on_ws_message
            )
            self.ws_collector.subscribe_ticker()
            self.ws_collector.subscribe_orderbook()
            logger.info(f"✅ WebSocket 호가창 구독 시작 | {len(self.settings.trading.target_markets)}개 코인")

            # 12. 초기 데이터 수집
            await self._initial_data_fetch()

            # 13. 메인 루프
            logger.info("🚀 메인 루프 시작")
            await self._main_loop()

        except KeyboardInterrupt:
            logger.info("🛑 사용자 종료 요청")
        except Exception as e:
            logger.error(f"❌ 엔진 치명적 오류: {e}")
            await self.telegram.notify_error(str(e), "메인 루프")
            raise
        finally:
            await self.stop()

    async def stop(self):
        """봇 정상 종료"""
        logger.info("🛑 APEX BOT 종료 중...")
        self.state_machine.transition(BotState.STOPPED)
        self.scheduler.shutdown(wait=False)
        self._process_pool.shutdown(wait=False)
        if self.ws_collector:
            await self.ws_collector.stop()
        await self.dashboard.stop()
        logger.info("✅ APEX BOT 정상 종료")

    def pause(self):
        """신규 거래 일시 중단 (긴급 명령)"""
        self.state_machine.transition(BotState.PAUSED)
        log_risk("PAUSE", "신규 거래 일시 중단")
        asyncio.create_task(self.telegram.notify_risk("PAUSE", "신규 거래 일시 중단"))

    def resume(self):
        """거래 재개"""
        self.state_machine.transition(BotState.RUNNING)
        logger.info("▶️ 거래 재개")

    # ── 외부 데이터 초기화 ────────────────────────────────────────
    async def _init_external_data(self):
        """김치 프리미엄, 공포탐욕 지수, 뉴스 초기 데이터 로드"""
        logger.info("🌐 외부 데이터 초기화 중...")
        tasks = [
            self.kimchi_monitor.fetch_all(),
            self.fear_greed.fetch(),
            self.news_analyzer.fetch_news(),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        names = ["김치 프리미엄", "공포탐욕 지수", "뉴스 감성"]
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.warning(f"{names[i]} 초기화 실패 (계속 진행): {r}")
        logger.info(
            f"  공포탐욕 지수: {self.fear_greed.index} ({self.fear_greed.label})"
        )
        logger.info(
            f"  뉴스 수집: "
            f"{results[2] if not isinstance(results[2], Exception) else 0}건"
        )

    # ── 메인 루프 ─────────────────────────────────────────────────

    # ── Daily Loss Circuit Breaker ───────────────────────────
    async def _check_circuit_breaker(self) -> bool:
        """일일 손실 한도 초과 시 신규 매수 차단. True=차단."""
        try:
            daily_loss_limit = getattr(
                self.settings.risk, "daily_loss_limit", 0.05
            )
            if not hasattr(self, "_day_start_balance"):
                self._day_start_balance = (self.portfolio.get_total_value(self.adapter._paper_balance.get('KRW', 0)))                     if hasattr(self.portfolio, "total_value")                     else self.settings.trading.min_order_amount * 200
                return False
            current  = ((self.portfolio.get_total_value(self.adapter._paper_balance.get('KRW', 0)))
                        if hasattr(self.portfolio, "total_value")
                        else self._day_start_balance)
            loss_pct = (self._day_start_balance - current) / max(self._day_start_balance, 1)
            if loss_pct >= daily_loss_limit:
                logger.warning(
                    f"🚨 Circuit Breaker 발동! "
                    f"일일 손실 {loss_pct:.1%} (한도 {daily_loss_limit:.1%}) "
                    f"— 신규 매수 차단"
                )
                return True
            return False
        except Exception as _e:
            logger.error(f"[circuit_breaker] {_e}")
            return False
    # ─────────────────────────────────────────────────────────

    async def _main_loop(self):
        """메인 이벤트 루프 (1분 주기)"""
        while self.state_machine.state != BotState.STOPPED:
            try:
                if self.state_machine.state == BotState.RUNNING:
                    with Timer("메인 루프 사이클"):
                        # ── circuit breaker 체크 ──────────────
                        if await self._check_circuit_breaker():
                            await asyncio.sleep(60)
                            continue
                        # ─────────────────────────────────────
                        await self._cycle()
                elif self.state_machine.state == BotState.PAUSED:
                    logger.debug("⏸️ 일시정지 중...")
                    await asyncio.sleep(10)
                    continue

                await asyncio.sleep(60)  # 1분 주기

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"루프 오류: {e}")
                await asyncio.sleep(30)

    async def _cycle(self):
        """단일 트레이딩 사이클"""
        # ✅ 분석 대상: 고정 10개 + 동적 스캐너 발굴 코인
        _base = list(self.settings.trading.target_markets)
        _dynamic = [m for m in getattr(self, '_dynamic_markets', []) if m not in _base]
        markets = _base + _dynamic
        # self.markets 동기화 (스캐너 결과 반영용)
        self.markets = markets

        # 현재가 일괄 수집
        price_tasks = [self.adapter.get_current_price(m) for m in markets]
        prices = await asyncio.gather(*price_tasks, return_exceptions=True)

        for i, market in enumerate(markets):
            if isinstance(prices[i], Exception) or prices[i] is None:
                continue
            p = prices[i]
            self._market_prices[market] = p
            # ✅ 상관관계 필터 가격 업데이트
            self.correlation_filter.update_price(market, p)
            # ✅ 김치 프리미엄 업데이트
            self.kimchi_monitor.update_upbit_price(market, p)

        # 포트폴리오 가격 업데이트
        self.portfolio.update_prices(self._market_prices)

        # 드로다운 체크
        krw = await self.adapter.get_balance("KRW")
        total_value = self.portfolio.get_total_value(krw)
        drawdown = self.portfolio.get_current_drawdown(total_value)

        if await self.risk_manager.check_circuit_breaker(drawdown, total_value):
            return  # 서킷브레이커 발동

        # ✅ 전체 마켓 스캐너 (급등 코인 동적 포착)
        try:
            new_surge_markets = await self._market_scanner()
            if new_surge_markets:
                for _sm in new_surge_markets:
                    if _sm not in markets:
                        markets.append(_sm)
                        self.markets = markets
                        logger.info(f"🔥 급등 코인 감시 추가: {_sm}")
        except Exception as _se:
            logger.debug(f"마켓 스캐너 오류: {_se}")

        # ✅ 트레일링 스탑 + 부분 청산 체크
        await self._check_position_exits()

        # 각 마켓 신호 분석 (비동기 병렬)
        # ── 전체 코인 분석 (포지션 수 무관하게 항상 실행) ──────────────
        # 신규 진입 후보: 포지션 없는 코인
        new_entry_markets = [
            m for m in markets
            if not self.portfolio.is_position_open(m)
        ]
        # 기존 포지션 코인: ML 재평가 + 익절/추가매수 탐색
        existing_markets = [
            m for m in markets
            if self.portfolio.is_position_open(m)
        ]
        # 신규 진입은 잔고와 포지션 수로만 제한 (분석은 항상 실행)
        can_enter_new = (
            self.portfolio.position_count < self.settings.trading.max_positions
            and krw >= self.settings.trading.min_order_amount
        )
        entry_tasks = [
            self._analyze_market(m)
            for m in new_entry_markets
        ] if can_enter_new else []
        exist_tasks = [
            self._analyze_existing_position(m)
            for m in existing_markets
        ]
        await asyncio.gather(*(entry_tasks + exist_tasks), return_exceptions=True)
        # ✅ GPU 배치 ML 추론 — 전체 코인 단일 forward pass
        try:
            _batch_df_map = {}
            for _bm in markets:
                _bdf = self.cache_manager.get_ohlcv(_bm)
                if _bdf is not None and len(_bdf) >= 60:
                    _batch_df_map[_bm] = _bdf
            if _batch_df_map:
                _batch_results = await self._get_ml_prediction_batch(_batch_df_map)
                # 배치 결과를 _analyze_market에서 활용할 수 있도록 캐시
                self._ml_batch_cache = _batch_results
        except Exception as _be:
            logger.debug(f"배치 ML 추론 스킵: {_be}")
            self._ml_batch_cache = {}

        # 대시보드 업데이트
        # ── ML 예측 독립 실행 (포지션 관계없이 BTC 기준으로 항상 실행) ──
        try:
            _ml_market = "KRW-BTC"
            # cache_manager에서 캔들 데이터 로드 (1h 우선, 없으면 1d)
            try:
                _ml_df = self.cache_manager.get_candles(_ml_market, "1h")
            except Exception:
                _ml_df = None
            if _ml_df is None or len(_ml_df) < 10:
                try:
                    _ml_df = self.cache_manager.get_candles(_ml_market, "1d")
                except Exception:
                    _ml_df = None
            # cache_manager에 없으면 _market_df 직접 탐색
            if _ml_df is None or len(_ml_df) < 10:
                for _attr in ["_df_cache", "_candle_cache", "_ohlcv_cache"]:
                    _cache = getattr(self, _attr, None)
                    if _cache and isinstance(_cache, dict):
                        _ml_df = _cache.get(f"{_ml_market}-1h") or _cache.get(_ml_market)
                        if _ml_df is not None:
                            break
            if _ml_df is not None and len(_ml_df) >= 50:
                _ml_result = await self._get_ml_prediction(_ml_market, _ml_df)
                if _ml_result:
                    from monitoring.dashboard import dashboard_state
                    _sig  = _ml_result.get("signal", "HOLD")
                    _conf = _ml_result.get("confidence", 0.0)
                    _bp   = _ml_result.get("buy_prob",  0.0)
                    _sp   = _ml_result.get("sell_prob", 0.0)
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
        except Exception as _ml_e:
            pass
        await self._update_dashboard_state(krw, total_value)


    async def _check_position_exits(self):
        """트레일링 스탑 + ATR 손절/익절 + 부분청산 + M4 청산 체크"""
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
                    getattr(pos, "avg_price", None)
                    or getattr(pos, "entry_price", None)
                    or (pos.get("avg_price") if isinstance(pos, dict) else None)
                    or 0
                )
                if entry_price <= 0:
                    continue

                # ✅ 0순위: ATR get_dynamic_levels 손절/익절
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
                                f"🔄 SL 동적 이동 ({market}): "
                                f"SL={basic_sl:,.0f} | "
                                f"수익={_profit_pct*100:.2f}% | "
                                f"RR={_sl_levels.rr_ratio:.2f}"
                            )
                    else:
                        basic_sl = entry_price * (1 - getattr(self.settings.risk, "stop_loss_pct", 0.03))
                        basic_tp = entry_price * (1 + getattr(self.settings.risk, "take_profit_pct", 0.05))
                except Exception as _dyn_e:
                    logger.debug(f"ATR 동적 계산 실패 ({market}): {_dyn_e}")
                    basic_sl = entry_price * (1 - getattr(self.settings.risk, "stop_loss_pct", 0.03))
                    basic_tp = entry_price * (1 + getattr(self.settings.risk, "take_profit_pct", 0.05))

                if current_price <= basic_sl:
                    loss_pct = (current_price - entry_price) / entry_price * 100
                    logger.info(
                        f"🔴 기본 손절 발동 ({market}): "
                        f"현재={current_price:,.0f} ≤ SL={basic_sl:,.0f} "
                        f"({loss_pct:.2f}%)"
                    )
                    await self._execute_sell(market, f"기본손절_{loss_pct:.1f}%", current_price)
                    continue

                if current_price >= basic_tp:
                    profit_pct = (current_price - entry_price) / entry_price * 100
                    logger.info(
                        f"🟢 기본 익절 발동 ({market}): "
                        f"현재={current_price:,.0f} ≥ TP={basic_tp:,.0f} "
                        f"({profit_pct:.2f}%)"
                    )
                    await self._execute_sell(market, f"기본익절_{profit_pct:.1f}%", current_price)
                    continue

                # ✅ 1순위: 트레일링 스탑 체크
                exit_reason = self.trailing_stop.update(market, current_price)
                if exit_reason:
                    await self._execute_sell(market, exit_reason, current_price)
                    continue

                # ✅ 2순위: 부분 청산 체크
                exit_volume = self.partial_exit.check(market, current_price)
                if exit_volume > 0:
                    await self._execute_partial_sell(market, exit_volume, current_price)

                # ✅ 3순위: M4 PositionManagerV2 청산 체크
                if self.position_mgr_v2 is not None:
                    try:
                        _exit_sig = self.position_mgr_v2.check_exit(market, current_price)
                        if _exit_sig.should_exit:
                            from risk.position_manager_v2 import ExitReason
                            logger.info(
                                f"⚡ M4 청산 신호 ({market}): "
                                f"사유={_exit_sig.reason.value} | "
                                f"비율={_exit_sig.volume_pct:.0%} | "
                                f"{_exit_sig.message}"
                            )
                            if _exit_sig.reason.value == "PARTIAL_EXIT":
                                _pos_v = self.portfolio.open_positions.get(market)
                                if _pos_v:
                                    _sell_vol = getattr(_pos_v, "volume", 0) * _exit_sig.volume_pct
                                    if _sell_vol > 0:
                                        await self._execute_partial_sell(market, _sell_vol, current_price)
                            else:
                                await self._execute_sell(
                                    market,
                                    f"M4_{_exit_sig.reason.value}",
                                    current_price,
                                )
                    except Exception as _m4_e:
                        logger.debug(f"M4 청산 체크 스킵 ({market}): {_m4_e}")

            except Exception as _e:
                logger.debug(f"포지션 청산 체크 오류 ({market}): {_e}")


    async def _analyze_existing_position(self, market: str) -> None:
        """기존 포지션 ML 재평가 – 익절/손절 시그널 감지"""
        try:
            pos = self.portfolio.get_position(market)
            if pos is None:
                return

            # 캔들 데이터 확보
            # NpyCache 우선, 없으면 REST로 직접 수집
            candles = self.cache_manager.get_ohlcv(market, "1h")
            if candles is None or (hasattr(candles, '__len__') and len(candles) < 20):
                try:
                    candles = await self.rest_collector.get_ohlcv(market, interval='minute60', count=100)
                except Exception:
                    candles = None
            # candles 길이 안전 체크 (DataFrame / list / None 모두 처리)
            try:
                _candle_len = len(candles) if candles is not None else 0
            except Exception:
                _candle_len = 0
            if _candle_len < 20:
                return

            # ML 예측
            ml_result = await self._get_ml_prediction(market, candles)
            if ml_result is None:
                return

            signal     = ml_result.get("signal", "HOLD")
            confidence = ml_result.get("confidence", 0.0)

            # 현재 PnL 계산
            # Position 객체 속성 안전 접근 (dataclass or dict 모두 지원)
            if hasattr(pos, 'avg_price'):
                entry_price = getattr(pos, 'avg_price', 0) or getattr(pos, 'entry_price', 0)
            elif hasattr(pos, 'entry_price'):
                entry_price = getattr(pos, 'entry_price', 0)
            elif isinstance(pos, dict):
                entry_price = pos.get("avg_price", pos.get("entry_price", 0))
            else:
                entry_price = 0
            current_price = self._market_prices.get(market, 0)
            pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0

            logger.debug(
                f"📊 포지션 재평가 | {market} | "
                f"ML={signal}({confidence:.2f}) | PnL={pnl_pct:+.2f}%"
            )

            # ✅ ATR 동적 손절/익절 체크 (get_dynamic_levels)
            if entry_price > 0 and current_price > 0 and _candle_len >= 20:
                try:
                    _profit_pct = (current_price - entry_price) / entry_price
                    _atr_levels = self.atr_stop.get_dynamic_levels(
                        candles, entry_price, current_price, _profit_pct
                    )
                    _basic_sl = _atr_levels.stop_loss
                    _basic_tp = _atr_levels.take_profit

                    # SL 동적 이동 로그 (수익 +3% 이상)
                    if _profit_pct >= 0.03:
                        logger.info(
                            f"🔄 SL 동적 이동 ({market}): "
                            f"SL={_basic_sl:,.0f} | "
                            f"수익={_profit_pct*100:.2f}% | "
                            f"RR={_atr_levels.rr_ratio:.2f}"
                        )

                    # ATR 손절 발동
                    if current_price <= _basic_sl:
                        _loss_pct = _profit_pct * 100
                        logger.info(
                            f"🔴 ATR 손절 발동 ({market}): "
                            f"현재={current_price:,.0f} ≤ SL={_basic_sl:,.0f} "
                            f"({_loss_pct:.2f}%)"
                        )
                        await self._execute_sell(
                            market,
                            f"ATR손절_{_loss_pct:.1f}%",
                            current_price
                        )
                        return

                    # ATR 익절 발동
                    if current_price >= _basic_tp:
                        _profit_pct2 = _profit_pct * 100
                        logger.info(
                            f"🟢 ATR 익절 발동 ({market}): "
                            f"현재={current_price:,.0f} ≥ TP={_basic_tp:,.0f} "
                            f"({_profit_pct2:.2f}%)"
                        )
                        await self._execute_sell(
                            market,
                            f"ATR익절_{_profit_pct2:.1f}%",
                            current_price
                        )
                        return

                except Exception as _atr_e:
                    logger.debug(f"ATR 동적 손절 체크 오류 ({market}): {_atr_e}")

            # 익절 조건: ML SELL 신뢰도 > 0.75, 수익 > 1%
            if signal == "SELL" and confidence > 0.75 and pnl_pct > 1.0:
                logger.info(
                    f"🎯 ML 익절 실행 | {market} | 신뢰도={confidence:.2f} | 수익={pnl_pct:+.2f}%"
                )
                await self._execute_sell(
                    market,
                    f"ML익절_{pnl_pct:.1f}%",
                    current_price,
                )
                return

        except Exception as e:
            import traceback
            logger.debug(f"포지션 재평가 오류 ({market}): {e} | {traceback.format_exc().splitlines()[-1]}")


    async def _analyze_market(self, market: str):
        from signals.signal_combiner import CombinedSignal, SignalType  # 스코프 보장
        """단일 마켓 분석 → 신호 생성 → 주문 실행"""
        # ✅ 포지션 한도 사전 체크 (병렬 실행 대응)
        if self.portfolio.position_count >= self.settings.trading.max_positions:
            return
        if self.portfolio.is_position_open(market):
            return
        # 신호 쿨다운 체크
        last_signal = self._last_signal_time.get(market, 0)
        # BEAR_REVERSAL 마켓은 쿨다운 60초로 단축
        _cooldown = (
            60 if market in getattr(self, '_bear_reversal_markets', set())
            else self._signal_cooldown
        )
        if time.time() - last_signal < _cooldown:
            return

        try:
            # ✅ 1. 상관관계 필터 (BTC 급락 시 알트 매수 차단)
            open_pos = list(self.portfolio.open_positions.keys())
            can_buy_corr, corr_reason = self.correlation_filter.can_buy(
                market, open_pos
            )
            if not can_buy_corr:
                logger.debug(f"상관관계 차단 ({market}): {corr_reason}")
                return

            # ✅ 2. 김치 프리미엄 체크
            can_buy_kimchi, kimchi_reason, premium = self.kimchi_monitor.can_buy(market)
            if not can_buy_kimchi:
                logger.debug(
                    f"김치 프리미엄 차단 ({market}): {kimchi_reason} "
                    f"[프리미엄 {premium:.1f}%]"
                )
                return

            # 3. 캔들 데이터 수집
            df_1h = await self.rest_collector.get_ohlcv(market, "minute60", 200)
            if df_1h is None or len(df_1h) < 50:
                return

            # ── Layer 2: TrendFilter — 일봉 EMA200 트렌드 체크 ──
            try:
                df_1d = await self.rest_collector.get_ohlcv(market, "day", 210)
                if df_1d is None or len(df_1d) < 5: raise ValueError('일봉 데이터 없음')
                _strategy_hint = (
                    "BEAR_REVERSAL"
                    if market in getattr(self, '_bear_reversal_markets', set())
                    else "default"
                )
                _trend = self.trend_filter.is_buy_allowed(
                    daily_df = df_1d,
                    strategy = _strategy_hint,
                )
                if not _trend["allowed"]:
                    logger.debug(
                        f"[TrendFilter] 매수 차단 ({market}): {_trend['reason']}"
                    )
                    return
                logger.debug(
                    f"[TrendFilter] {market}: {_trend['reason']} "
                    f"(레짐={_trend.get('regime','?')})"
                )
            except Exception as _te:
                logger.debug(f'[TrendFilter] 스킵 ({market}): {_te}')
            # ── Layer 2-B: VolumeProfile — POC/VAH/VAL 지지저항 체크 ──
            try:
                _vp = self.volume_profile.analyze(df_1h)
                if _vp is not None:
                    _cur_price = float(df_1h['close'].iloc[-1])
                    _vp_sr = self.volume_profile.get_nearest_support_resistance(
                        df_1h, _cur_price
                    )
                    _rr = _vp_sr.get('risk_reward', 1.0)
                    _sup = _vp_sr.get('support', 0)
                    _res = _vp_sr.get('resistance', 0)
                    # risk_reward < 0.5 → 저항이 지지보다 훨씬 가까움 → 매수 불리
                    if _rr < 0.5 and _sup > 0 and _res > 0:
                        logger.info(
                            f'[VolumeProfile] 매수 불리 ({market}): '
                            f'RR={_rr:.2f} 저항={_res:,.0f} 지지={_sup:,.0f}'
                        )
                        return
                    logger.info(
                        f'[VolumeProfile] {market}: POC={_vp.poc_price:,.0f} '
                        f'VAH={_vp.vah:,.0f} VAL={_vp.val:,.0f} RR={_rr:.2f}'
                    )
            except Exception as _ve:
                logger.debug(f'[VolumeProfile] 스킵 ({market}): {_ve}')


            # 4. 지표 계산
            df_processed = await self.candle_processor.process(market, df_1h, "60")
            if df_processed is None:
                return

            # 5. 시장 레짐 감지
            # ✅ Step 3: BEAR_REVERSAL 감지를 위해 공포탐욕 지수 전달
            regime = self.regime_detector.detect(
                market, df_processed,
                fear_greed_index=self.fear_greed.index,
            )
            # ✅ Step 3: BEAR_REVERSAL 예외 → 역발상 매수 허용
            if regime == MarketRegime.TRENDING_DOWN:
                return  # 일반 하락 추세 → 매수 금지
            if regime == MarketRegime.BEAR_REVERSAL:
                logger.info(
                    f"🔄 BEAR_REVERSAL 감지 ({market}) → "
                    f"역발상 매수 탐색 (포지션 50% 축소)"
                )
                self._bear_reversal_markets = getattr(
                    self, '_bear_reversal_markets', set()
                )
                self._bear_reversal_markets.add(market)
            else:
                self.bear_reversal_markets = getattr(
                    self, '_bear_reversal_markets', set()
                )
                self.bear_reversal_markets.discard(market)

            # ✅ 6. 거래량 스파이크 감지 (덤핑 방어)
            is_dumping, dump_reason = self.volume_spike.is_dumping(df_processed, market)
            _is_bear_rev = market in getattr(self, '_bear_reversal_markets', set())
            _in_pyramid = getattr(self, '_current_pyramid_market', None) == market
            if is_dumping and not _is_bear_rev and not _in_pyramid:
                # BEAR_REVERSAL 시에는 덤핑 차단 면제 (역발상 매수 허용)
                logger.debug(f"덤핑 감지 ({market}): {dump_reason}")
                return
            elif is_dumping and _is_bear_rev:
                logger.debug(
                    f"⚡ BEAR_REVERSAL 덤핑 면제 ({market}): {dump_reason}"
                )

            # 7. 전략별 신호 생성 (병렬)
            signals = await self._run_strategies(market, df_processed)

            # 8. ML 예측 (BiLSTM + TFT + CNN-LSTM 앙새마블)
            ml_pred = await self._get_ml_prediction(market, df_processed)

            # ✅ 8b. PPO 강화학습 신호 병합
            ppo_pred = await self._get_ppo_prediction(market, df_processed)
            if ppo_pred and ml_pred:
                # ML + PPO 소프트 보팅 (0.6:0.4)
                ml_conf = ml_pred.get("confidence", 0)
                ppo_conf = ppo_pred.get("confidence", 0)
                if ml_pred.get("signal") == ppo_pred.get("signal"):
                    # 두 모델 동의 → 신뢰도 강화
                    ml_pred["confidence"] = min(1.0, ml_conf * 0.6 + ppo_conf * 0.4 + 0.05)
                    ml_pred["ppo_agreement"] = True
                else:
                    # 불일치 → 패널티 제거, PPO=BUY이면 신뢰도 보완
                    ppo_signal = str(ppo_pred.get("action", ppo_pred.get("signal", ""))).upper()
                    if ppo_signal == "BUY":
                        ml_pred["confidence"] = min(1.0, ml_conf + ppo_conf * 0.30)
                    else:
                        ml_pred["confidence"] = ml_conf
                    ml_pred["ppo_agreement"] = False
                logger.debug(
                    f"ML+PPO 결합 ({market}): "
                    f"ML={ml_pred.get('signal','?')}({ml_conf:.2f}) | "
                    f"PPO={ppo_pred.get('action','?')}({ppo_conf:.2f}) | "
                    f"일치={ml_pred.get('ppo_agreement',False)}"
                )
            elif ppo_pred and ml_pred is None:
                # ML 없으면 PPO만 사용
                ml_pred = ppo_pred

            # ✅ 9. 공포탐욕 지수로 ML 신호 보정
            fg_adj = self.fear_greed.get_signal_adjustment()
            # ✅ FIX: Extreme Greed 90+ 매수 차단
            if fg_adj.get("block_buy", False):
                logger.info(
                    f"공포탐욕 매수 차단 ({market}): "
                    f"지수={self.fear_greed.index} ({self.fear_greed.label})"
                )
                return
            if ml_pred and fg_adj.get("mode") == "suppressed":
                # 극도 탐욕 → ML 신호 억제
                if ml_pred.get("confidence", 0) < 0.35:
                    logger.debug(
                        f"공포탐욕 억제 ({market}): "
                        f"지수={self.fear_greed.index} ({self.fear_greed.label})"
                    )
                    return

            # ✅ 10. 거래량 스파이크 → 신호 신뢰도 보정
            spike_info = self.volume_spike.detect(df_processed, market)
            vol_confidence_adj = self.volume_spike.get_confidence_adjustment(spike_info)

            # 11. 신호 결합
            combined = self.signal_combiner.combine(
                signals, market, ml_pred, regime.value
            )

            if combined is None:
                # ✅ BEAR_REVERSAL도 max_positions 한도 체크
                if self.portfolio.position_count >= self.settings.trading.max_positions:
                    logger.info(
                        f"⛔ BEAR_REVERSAL 한도 초과 ({market}): "
                        f"{self.portfolio.position_count}/{self.settings.trading.max_positions}"
                    )
                    return
                # BEAR_REVERSAL 마켓은 신호 없어도 약한 BUY 생성
                if market in getattr(self, '_bear_reversal_markets', set()):
                    # 하루 BEAR_REVERSAL 최대 3회 제한
                    _today = datetime.now().strftime('%Y-%m-%d')
                    _bear_count_key = f'_bear_rev_count_{_today}'
                    _bear_count = getattr(self, _bear_count_key, 0)
                    if _bear_count >= 6:
                        logger.info(f'⛔ BEAR_REVERSAL 일일 한도 초과 ({market}): {_bear_count}/6 → 강제 BUY 차단')
                        return
                    # 포지션이 max_positions의 50% 이상이면 차단
                    _max_p = self.settings.trading.max_positions
                    if self.portfolio.position_count >= int(_max_p * 0.5):
                        logger.info(f'⛔ BEAR_REVERSAL 포지션 50% 한도 ({market}): {self.portfolio.position_count}/{int(_max_p*0.5)} → 차단')
                        return
                    # 손절 쿨다운 체크 - 손절 후 4시간 재매수 금지
                    if hasattr(self, '_sl_cooldown') and market in self._sl_cooldown:
                        import datetime as _dt2
                        if _dt2.datetime.now() < self._sl_cooldown[market]:
                            remaining = int((self._sl_cooldown[market] - _dt2.datetime.now()).total_seconds() // 60)
                            logger.info(f'⏳ 손절 쿨다운 중 ({market}): {remaining}분 후 재매수 가능')
                            return
                        else:
                            del self._sl_cooldown[market]
                    # Fear&Greed가 15 이상이면 BEAR_REVERSAL 강제 BUY 비활성
                    _fg_idx = getattr(self.fear_greed, 'index', 50)
                    if _fg_idx > 15:
                        logger.info(f'⛔ BEAR_REVERSAL 공포탐욕 조건 불충족 ({market}): 지수={_fg_idx} > 15 → 강제 BUY 차단')
                        return
                    setattr(self, _bear_count_key, _bear_count + 1)
                    logger.info(f'⚡ BEAR_REVERSAL 강제 BUY 신호 생성 ({market}): 오늘 {_bear_count+1}/3회')
                    combined = CombinedSignal(
                        market                 = market,
                        signal_type            = SignalType.BUY,
                        score                  = 0.45,
                        confidence             = 0.45,
                        agreement_rate         = 1.0,
                        contributing_strategies= ['BEAR_REVERSAL'],
                        reasons                = ['극단적 공포 역발상 매수'],
                    )
            # ✅ combined None 방어 (신호 없으면 스킵)
            if combined is None:
                return
            # ✅ 거래량 보정 반영
            if vol_confidence_adj > 0:
                combined.confidence = min(1.0, combined.confidence * (1 + vol_confidence_adj))
                logger.debug(
                    f"거래량 스파이크 보정 ({market}): "
                    f"+{vol_confidence_adj:.2%} 신뢰도 향상"
                )

            # ✅ Step 3: 호가창 신호 분석 (orderbook_analyzer 없으면 통과)
            ob_analyzer = getattr(self, 'orderbook_analyzer', None)
            if ob_analyzer is not None:
                try:
                    ob_data = self.cache_manager.get_orderbook(market)
                    ob_signal = ob_analyzer.analyze(market, ob_data)
                    can_buy_ob, ob_reason = ob_analyzer.can_buy(ob_signal)
                    if not can_buy_ob and combined.signal_type == SignalType.BUY:
                        logger.debug(f"호가창 차단 ({market}): {ob_reason}")
                        return
                    ob_adj = ob_analyzer.get_confidence_adjustment(
                        ob_signal, trade_side="BUY"
                    )
                    if abs(ob_adj) > 0.01:
                        combined.confidence = min(1.0, combined.confidence * (1 + ob_adj))
                        logger.debug(
                            f"호가창 보정 ({market}): {ob_adj:+.2%} "
                            f"→ 신뢰도={combined.confidence:.2f}"
                        )
                except Exception as ob_e:
                    logger.debug(f"호가창 분석 스킵 ({market}): {ob_e}")
            else:
                logger.debug(f"호가창 분석기 없음 ({market}) → 통과")

            # ✅ 11. 뉴스 감성 분석 필터
            can_buy_news, news_reason = self.news_analyzer.can_buy(market)
            if not can_buy_news and combined.signal_type == SignalType.BUY:
                logger.debug(f"뉴스 감성 차단 ({market}): {news_reason}")
                return

            # 뉴스 기반 신호 보정 (ScoreBoost)
            news_score, news_boost = self.news_analyzer.get_signal_boost(market)
            if abs(news_boost) > 0.3:
                original_score = combined.score
                combined.score = combined.score - news_boost  # boost>0 = 억제
                logger.debug(
                    f"뉴스 점수 보정 ({market}): "
                    f"{original_score:.2f} → {combined.score:.2f} "
                    f"(boost={news_boost:+.2f}, 감성={news_score:+.3f})"
                )

            log_signal(
                market, combined.signal_type.name, combined.score,
                combined.contributing_strategies
            )
            # ── Layer 3: M3 MTF 신호 합산 필터 ─────────────
            if self.mtf_merger is not None:
                try:
                    # 보유 중인 TF 데이터로 MTF 분석
                    _tf_data = {}
                    _cached_1h = self.cache_manager.get_candles(market, "1h")
                    if _cached_1h is not None and len(_cached_1h) > 5:
                        _tf_data["1h"] = _cached_1h
                    # 일봉 캐시 시도
                    _cached_1d = self.cache_manager.get_candles(market, "1d")
                    if _cached_1d is not None and len(_cached_1d) > 5:
                        _tf_data["1d"] = _cached_1d
                    if _tf_data:
                        _mtf_result = self.mtf_merger.analyze(_tf_data)
                        # BUY 신호인데 MTF가 강한 하락 → 차단
                        if (combined.signal_type == SignalType.BUY
                                and _mtf_result.final_direction.value <= -1
                                and not _is_bear_rev):
                            logger.debug(
                                f"MTF 차단 ({market}): "
                                f"방향={_mtf_result.final_direction.name} | "
                                f"{_mtf_result.reason}"
                            )
                            return
                        logger.debug(
                            f"MTF 통과 ({market}): {_mtf_result.reason}"
                        )
                except Exception as _mtf_e:
                    logger.debug(f"MTF 분석 스킵 ({market}): {_mtf_e}")

            # ✅ DB signal_log 저장
            try:
                await self.db_manager.log_signal({
                    "market":     market,
                    "signal_type": combined.signal_type.name,
                    "score":      combined.score,
                    "confidence": combined.confidence,
                    "strategies": combined.contributing_strategies,
                    "regime":     getattr(combined, "regime", ""),
                    "executed":   False,
                })
            except Exception as _sig_e:
                logger.debug(f"signal_log DB 저장 스킵: {_sig_e}")

            # ✅ BEAR_REVERSAL: HOLD 신호를 BUY로 상향 (역발상 매수)
            _is_bear_rev = market in getattr(self, '_bear_reversal_markets', set())
            if _is_bear_rev and combined.signal_type != SignalType.SELL:
                if combined.signal_type != SignalType.BUY:
                    logger.info(
                        f"⚡ BEAR_REVERSAL 신호 상향 ({market}): "
                        f"{combined.signal_type.name} → BUY (score={combined.score:.2f})"
                    )
                    combined.signal_type = SignalType.BUY
                    combined.score       = max(combined.score, 0.45)
                    combined.confidence  = max(combined.confidence, 0.45)
                # BEAR_REVERSAL 포지션 50% 축소 적용
                combined.bear_reversal = True

            # ── ⑥ 오더블록 탐지 ─────────────────────────────────────
            try:
                _ob_df = self.cache_manager.get_candles(market, "1h")
                if _ob_df is not None and len(_ob_df) >= 30:
                    _ob_price = float(df_processed["close"].iloc[-1])
                    _ob_sig = self.ob_detector.detect(_ob_df, _ob_price)
                    if _ob_sig.signal == "SELL_ZONE" and _ob_sig.confidence >= 0.5:
                        if combined.signal_type == SignalType.BUY:
                            logger.info(
                                f"🏛️ 오더블록 SELL_ZONE 매수 차단 ({market}): "
                                f"신뢰도={_ob_sig.confidence:.2f} "
                                f"거리={_ob_sig.dist_bearish_pct:.1f}%"
                            )
                            return
                    if _ob_sig.signal == "BUY_ZONE" and _ob_sig.confidence >= 0.4:
                        logger.info(
                            f"🏛️ 오더블록 BUY_ZONE ({market}): "
                            f"신뢰도={_ob_sig.confidence:.2f} "
                            f"거리={_ob_sig.dist_bullish_pct:.1f}%"
                        )
            except Exception as _ob_e:
                logger.debug(f"오더블록 탐지 스킵 ({market}): {_ob_e}")
            # 12. 매수 실행
            if combined.signal_type == SignalType.BUY:
                # 포지션 이미 있으면 스킵 (중복 매수 방지)
                if market not in self.portfolio.open_positions:
                    await self._execute_buy(market, combined, df_processed)
                    self._last_signal_time[market] = time.time()
                else:
                    logger.debug(f"이미 포지션 보유 ({market}) → 중복 매수 스킵")

        except Exception as e:
            logger.error(f"마켓 분석 오류 ({market}): {e}")

    def _get_preferred_strategies(self, market: str) -> list:
        """
        백테스트 기반 코인별 최적 전략 목록 반환
        하락장(EMA200 아래): bollinger_squeeze + VWAP_Reversion 우선 (rsi_divergence 제거)
        상승장(EMA200 위):  trend_following + ml_strategy 우선
        """
        # 전략명 매핑 (engine 내부 NAME → 백테스트 키)
        # 실제 전략 NAME:
        # macd_cross / rsi_divergence / Supertrend
        # bollinger_squeeze / VWAP_Reversion / volatility_breakout
        # ATR_Channel / OrderBlock_SMC
        BEAR_PREFERRED = {
            # rsi_divergence 제거 (백테스트 -10.0%)
            "KRW-BTC":  ["macd_cross",         "Supertrend"],
            "KRW-ETH":  ["bollinger_squeeze",   "VWAP_Reversion"],
            "KRW-XRP":  ["bollinger_squeeze",   "VWAP_Reversion"],
            "KRW-SOL":  ["VWAP_Reversion",      "bollinger_squeeze"],
            "KRW-ADA":  ["bollinger_squeeze",   "VWAP_Reversion"],
            "KRW-DOGE": ["bollinger_squeeze",   "macd_cross"],
            "KRW-DOT":  ["bollinger_squeeze",   "VWAP_Reversion"],
            "KRW-LINK": ["VWAP_Reversion",      "bollinger_squeeze"],
            "KRW-AVAX": ["VWAP_Reversion",      "bollinger_squeeze"],
            "KRW-ATOM": ["bollinger_squeeze",   "VWAP_Reversion"],
        }
        BULL_PREFERRED = {
            # rsi_divergence 제거 (백테스트 -10.0%), volatility_breakout → VWAP_Reversion
            "KRW-BTC":  ["macd_cross",         "Supertrend"],
            "KRW-ETH":  ["Supertrend",          "VWAP_Reversion"],
            "KRW-XRP":  ["Supertrend",          "macd_cross"],
            "KRW-SOL":  ["Supertrend",          "macd_cross"],
            "KRW-ADA":  ["Supertrend",          "bollinger_squeeze"],
            "KRW-DOGE": ["bollinger_squeeze",   "macd_cross"],
            "KRW-DOT":  ["Supertrend",          "VWAP_Reversion"],
            "KRW-LINK": ["Supertrend",          "VWAP_Reversion"],
            "KRW-AVAX": ["Supertrend",          "VWAP_Reversion"],
            "KRW-ATOM": ["VWAP_Reversion",      "Supertrend"],
        }
        # 현재 국면 판단 (TrendFilter 결과 캐시 활용)
        is_bull = market not in getattr(self, "_bear_reversal_markets", set())
        preferred = (BULL_PREFERRED if is_bull else BEAR_PREFERRED).get(
            market, list(self._strategies.keys())
        )
        # 선택된 전략이 실제 로드된 전략에 있는 것만 반환
        available = [n for n in preferred if n in self._strategies]
        # 선택된 전략이 없으면 전체 사용 (폴백)
        if not available:
            available = list(self._strategies.keys())
        return available

    async def _run_strategies(self, market: str, df) -> list:
        """코인별 최적 전략만 실행 (백테스트 기반 매핑 적용)"""
        signals = []
        tasks   = []
        # 코인별 선호 전략 선택
        preferred = self._get_preferred_strategies(market)
        selected  = {n: s for n, s in self._strategies.items() if n in preferred}
        # 선택된 전략이 없으면 전체 실행 (폴백)
        if not selected:
            selected = self._strategies
        logger.debug(
            f"전략 선택 ({market}): {list(selected.keys())} "
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
                    f"전략 실행 오류 ({market}): {type(result).__name__}: {result}"
                )
            elif result:
                signals.append(result)
                logger.debug(
                    f"전략 신호 생성 ({market}): "
                    f"signal={getattr(result, 'signal', '?')} "
                    f"score={getattr(result, 'score', 0):.2f} "
                    f"strategy={getattr(result, 'strategy_name', '?')}"
                )
        if not signals:
            logger.debug(
                f"전략 신호 없음 ({market}): "
                f"0/{len(selected)}개 전략에서 신호 없음"
            )
        return signals

    async def _get_ml_prediction(self, market: str, df) -> Optional[dict]:
        """ML 앙상블 예측 + 대시보드 실시간 기록"""
        if self._ml_predictor is None:
            return None
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._ml_predictor.predict, market, df
            )
            # ── 대시보드 signals에 ML 예측 결과 실시간 기록 ──
            if result:
                from monitoring.dashboard import dashboard_state
                from datetime import datetime
                # 1) 먼저 _ml_pred_data 초기화 (UnboundLocalError 방지)
                _sig  = result.get('signal', 'HOLD')
                _conf = result.get('confidence', 0.0)
                _bp   = result.get('buy_prob',  0.0)
                _sp   = result.get('sell_prob', 0.0)
                _ml_pred_data = {
                    'signal':     _sig,
                    'confidence': round(float(_conf), 3),
                    'buy_prob':   round(float(_bp),   3),
                    'sell_prob':  round(float(_sp),   3),
                    'market':     market,
                }
                # 2) 대시보드 상태 업데이트
                if 'ml_predictions' not in dashboard_state.signals:
                    dashboard_state.signals['ml_predictions'] = {}
                dashboard_state.signals['ml_predictions'][market] = {
                    'signal':          result.get('signal'),
                    'confidence':      round(result.get('confidence', 0), 4),
                    'buy_prob':        round(result.get('buy_prob', 0), 4),
                    'hold_prob':       round(result.get('hold_prob', 0), 4),
                    'sell_prob':       round(result.get('sell_prob', 0), 4),
                    'model_agreement': round(result.get('model_agreement', 0), 4),
                    'inference_ms':    round(result.get('inference_ms', 0), 2),
                    'updated_at':      datetime.now().strftime('%H:%M:%S'),
                }
                dashboard_state.signals['ml_predictions'][market] = _ml_pred_data
                dashboard_state.signals['ml_prediction']  = _ml_pred_data
                dashboard_state.signals['ml_last_updated'] = datetime.now().isoformat()
                dashboard_state.signals['ml_model_loaded'] = self._ml_predictor._is_loaded
            return result
        except Exception as e:
            logger.error(f"ML 예측 오류 ({market}): {e}")
            return None

    async def _get_ml_prediction_batch(self, market_df_map: dict) -> dict:
        """
        ✅ GPU 배치 추론 — 10개 코인을 단일 forward pass로 처리
        predict_batch() 활용으로 순차 추론 대비 ~8배 속도 향상
        반환: {market: {signal, confidence, buy_prob, ...}}
        """
        if self._ml_predictor is None:
            return {}
        try:
            t_start = __import__("time").perf_counter()
            results = await asyncio.get_event_loop().run_in_executor(
                None,
                self._ml_predictor.predict_batch,
                market_df_map,
            )
            elapsed = (__import__("time").perf_counter() - t_start) * 1000
            if results:
                logger.info(
                    f"⚡ 배치 ML 추론 완료: {len(results)}개 코인 | "
                    f"{elapsed:.1f}ms | "
                    f"코인당 {elapsed/len(results):.1f}ms"
                )
                # 대시보드 일괄 업데이트
                try:
                    from monitoring.dashboard import dashboard_state
                    from datetime import datetime
                    if "ml_predictions" not in dashboard_state.signals:
                        dashboard_state.signals["ml_predictions"] = {}
                    for mkt, res in results.items():
                        dashboard_state.signals["ml_predictions"][mkt] = {
                            "signal":          res.get("signal"),
                            "confidence":      round(res.get("confidence", 0), 4),
                            "buy_prob":        round(res.get("buy_prob", 0), 4),
                            "hold_prob":       round(res.get("hold_prob", 0), 4),
                            "sell_prob":       round(res.get("sell_prob", 0), 4),
                            "model_agreement": round(res.get("model_agreement", 0), 4),
                            "updated_at":      datetime.now().strftime("%H:%M:%S"),
                        }
                    dashboard_state.signals["ml_last_updated"] = datetime.now().isoformat()
                    dashboard_state.signals["ml_model_loaded"] = self._ml_predictor._is_loaded
                except Exception as _db_e:
                    logger.debug(f"배치 ML 대시보드 업데이트 스킵: {_db_e}")
            return results
        except Exception as e:
            logger.warning(f"배치 ML 추론 실패 → 개별 추론으로 폴백: {e}")
            return {}

    async def _get_ppo_prediction(self, market: str, df) -> Optional[dict]:
        """PPO 강화학습 에이전트 예측"""
        if self._ppo_agent is None or not self._ppo_agent._is_trained:
            return None
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._ppo_agent.predict_from_df(df, market)
            )
        except Exception as e:
            logger.debug(f"PPO 예측 오류 ({market}): {e}")
            return None

    async def _execute_buy(self, market: str, signal: CombinedSignal, df):
        """매수 주문 실행"""
        # ✅ 포지션 한도 체크 (병렬 실행 대응)
        _max_pos = self.settings.trading.max_positions
        if self.portfolio.position_count >= _max_pos:
            logger.info(
                f"⛔ 포지션 한도 ({market}): "
                f"{self.portfolio.position_count}/{_max_pos} → 매수 취소"
            )
            return
        if self.portfolio.is_position_open(market):
            logger.debug(f"⛔ 중복 매수 차단 ({market}): 이미 포지션 존재")
            return
        # ✅ Race Condition 방지: 진행 중인 매수 체크
        if market in self._buying_markets:
            logger.debug(f"⛔ 중복 매수 차단 ({market}): 매수 진행 중")
            return
        self._buying_markets.add(market)
        # ── SmartWallet: HOLD 체크 ──────────────────────────
        _symbol = market.replace("KRW-", "")
        _can_buy, _buy_note = self._wallet.can_buy(_symbol)
        if not _can_buy:
            logger.warning(f"🔒 SmartWallet 매수 차단: {_buy_note}")
            return
        logger.info(f"🟢 SmartWallet: {_buy_note}")
        # ────────────────────────────────────────────────────
        # 리스크 검증
        krw = await self.adapter.get_balance("KRW")
        can_buy, reason = await self.risk_manager.can_open_position(
            market, krw, self.portfolio.position_count
        )
        if not can_buy:
            logger.info(f"매수 차단 ({market}): {reason}")
            return

        # BEAR_REVERSAL 신호는 score 임계값 체크 면제
        _is_bear_rev_signal = 'BEAR_REVERSAL' in getattr(signal, 'contributing_strategies', [])
        if not _is_bear_rev_signal:
            # ✅ 공포탐욕 지수로 매수 임계값 보정
            fg_threshold_adj = self.fear_greed.get_buy_threshold_adjustment()
            if signal.score < (self.settings.risk.buy_signal_threshold + fg_threshold_adj):
                logger.debug(
                    f'공포탐욕 임계값 조정 차단 ({market}): '
                    f'점수={signal.score:.2f} < '
                    f'임계={self.settings.risk.buy_signal_threshold + fg_threshold_adj:.2f} '
                    f'(조정={fg_threshold_adj:+.2f})'
                )
                return

        # 포지션 사이징 (Kelly Criterion)
        last = df.iloc[-1]
        # ✅ ATRStopLoss로 정확한 SL/TP 계산 (pandas_ta 컬럼 의존 제거)
        try:
            _sl_levels_buy = self.atr_stop.calculate(df, float(last["close"]))
            atr        = _sl_levels_buy.atr
            stop_loss  = _sl_levels_buy.stop_loss
            take_profit= _sl_levels_buy.take_profit
            logger.info(
                f"📐 ATR-SL ({market}): SL={stop_loss:,.0f} "
                f"({_sl_levels_buy.sl_pct*100:.2f}%) | "
                f"TP={take_profit:,.0f} ({_sl_levels_buy.tp_pct*100:.2f}%) | "
                f"RR={_sl_levels_buy.rr_ratio:.2f} | ATR={atr:,.0f}"
            )
        except Exception as _atr_e:
            logger.warning(f"⚠️ ATR 계산 실패 ({market}): {_atr_e} → 고정비율 사용")
            atr         = float(last["close"]) * 0.02
            stop_loss   = float(last["close"]) * (1 - self.settings.risk.atr_stop_multiplier * 0.01)
            take_profit = float(last["close"]) * (1 + self.settings.risk.atr_target_multiplier * 0.01)
        # ── Layer 2: KellyPositionSizer — 동적 포지션 사이징 ──
        _strategy_name = getattr(signal, 'contributing_strategies', ['default'])
        _strategy_name = _strategy_name[0] if _strategy_name else 'default'
        _ml_conf       = getattr(signal, 'ml_confidence', 0.5)
        position_size  = self.position_sizer.calculate(
            total_capital = krw,
            strategy      = _strategy_name,
            market        = market,
            confidence    = _ml_conf,
        )
        # ✅ BEAR_REVERSAL: 포지션 50% 축소 (리스크 관리)
        if getattr(signal, 'bear_reversal', False):
            position_size *= 0.5
            logger.info(
                f"⚡ BEAR_REVERSAL 포지션 50% 축소 ({market}): "
                f"₩{position_size*2:,.0f} → ₩{position_size:,.0f}"
            )
        if position_size < self.settings.trading.min_order_amount:
            logger.debug(
                f"포지션 너무 작음 ({market}): "
                f"₩{position_size:,.0f} < 최소 ₩{self.settings.trading.min_order_amount:,.0f}"
            )
            return

        # ✅ max_positions 초과 시 매수 차단
        current_pos_count = self.portfolio.position_count
        max_pos = self.settings.trading.max_positions
        if current_pos_count >= max_pos:
            logger.info(f"⛔ 포지션 한도 초과 ({market}): {current_pos_count}/{max_pos} → 매수 취소")
            return

        # ✅ stop_loss / take_profit → ATR 계산 블록에서 이미 산출됨
        # (L1188 블록 참조)

        req = ExecutionRequest(
            market=market,
            side=OrderSide.BUY,
            amount_krw=position_size,
            reason=signal.reasons[0] if signal.reasons else "앙상블 매수",
            strategy_name=", ".join(signal.contributing_strategies),
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        try:
            result = await self.executor.execute(req)
        finally:
            self._buying_markets.discard(market)  # ✅ 매수 완료/실패 시 항상 해제
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

            # ✅ 트레일링 스탑 등록
            self.trailing_stop.add_position(
                market, result.executed_price, stop_loss, atr
            )

            # ── Layer 3: M4 PositionManagerV2 등록 ──────
            if self.position_mgr_v2 is not None:
                try:
                    from risk.position_manager_v2 import PositionV2
                    _pos_v2 = PositionV2(
                        market      = market,
                        entry_price = result.executed_price,
                        volume      = result.executed_volume,
                        amount_krw  = position_size,
                        stop_loss   = stop_loss,
                        take_profit = take_profit,
                        strategy    = req.strategy_name,
                    )
                    self.position_mgr_v2.add_position(_pos_v2)
                    logger.debug(
                        f"M4 PositionV2 등록 ({market}): "
                        f"BE트리거={stop_loss:,.0f} | "
                        f"부분익절1={take_profit*0.6:,.0f}"
                    )
                except Exception as _pv2_e:
                    logger.debug(f"PositionManagerV2 등록 스킵: {_pv2_e}")

            # ✅ 부분 청산 등록
            self.partial_exit.add_position(
                market=market,
                entry_price=result.executed_price,
                volume=result.executed_volume,
                take_profit=take_profit,
            )

            # ✅ fee 계산 (업비트 0.05%)
            _fee_rate  = getattr(self.settings.trading, 'fee_rate', 0.0005)
            _buy_fee   = position_size * _fee_rate

            log_trade("BUY", market, result.executed_price, position_size,
                      req.reason)
            await self.telegram.notify_buy(
                market, result.executed_price, position_size,
                req.reason, req.strategy_name
            )
            # ✅ DB trade_history 저장 (BUY)
            try:
                await self.db_manager.insert_trade({
                    "timestamp":  datetime.now().isoformat(),
                    "market":     market,
                    "side":       "BUY",
                    "price":      result.executed_price,
                    "volume":     result.executed_volume,
                    "amount_krw": position_size,
                    "fee":        _buy_fee,
                    "profit_rate": 0.0,
                    "strategy":   req.strategy_name,
                    "reason":     req.reason,
                })
            except Exception as _db_e:
                logger.debug(f"BUY DB 저장 스킵: {_db_e}")

            # ✅ signal_log executed=True 업데이트 (최근 신호)
            try:
                await self.db_manager.log_signal({
                    "market":      market,
                    "signal_type": "BUY",
                    "score":       getattr(signal, "score", 0),
                    "confidence":  getattr(signal, "confidence", 0),
                    "strategies":  list(getattr(signal, "contributing_strategies", [])),
                    "regime":      getattr(signal, "regime", ""),
                    "executed":    True,
                })
            except Exception as _sl_e:
                logger.debug(f"signal_log executed 업데이트 스킵: {_sl_e}")
        # ── SmartWallet: 매수 기록 ──────────────────────────
        try:
            _exec_price = float(getattr(result, 'executed_price',
                          getattr(result, 'price', 0)))
            _exec_qty   = float(getattr(result, 'executed_volume',
                          getattr(result, 'quantity',
                          getattr(result, 'qty', 0))))
            if _exec_qty > 0 and _exec_price > 0:
                self._wallet.record_buy(_symbol, _exec_qty, _exec_price)
        except Exception as _we:
            logger.debug(f"SmartWallet record_buy 스킵: {_we}")
    async def _execute_partial_sell(
        self, market: str, volume: float, current_price: float
    ):
        """부분 청산 실행"""
        pos = self.portfolio.get_position(market)
        if not pos or volume <= 0:
            return

        # 최소 주문 수량 체크
        if volume * current_price < self.settings.trading.min_order_amount:
            logger.debug(f"부분 청산 수량 부족 ({market}): {volume:.6f}")
            return

        state = self.partial_exit.get_state(market)
        executed_levels = sum(1 for lv in state.levels if lv.executed) if state else 0
        reason = f"부분청산_step{executed_levels}"

        req = ExecutionRequest(
            market=market,
            side=OrderSide.SELL,
            amount_krw=0,
            volume=volume,
            reason=reason,
            strategy_name=getattr(self.portfolio.get_position(market), 'strategy', 'unknown') or 'unknown',
        )

        result = await self.executor.execute(req)
        if result.executed_price > 0:
            profit_rate = (result.executed_price - pos.entry_price) / pos.entry_price

            # 포트폴리오 볼륨 감소
            pos.volume -= volume
            if pos.volume <= 0:
                self.portfolio.close_position(market, result.executed_price, result.fee, reason)
                self.trailing_stop.remove_position(market)
                self.partial_exit.remove_position(market)
            else:
                logger.info(
                    f"✂️ 부분 청산 완료 | {market} | "
                    f"가격={result.executed_price:,.0f} | "
                    f"수량={volume:.6f} | "
                    f"수익={profit_rate:.2%} | "
                    f"잔량={pos.volume:.6f}"
                )

            log_trade("PARTIAL_SELL", market, result.executed_price,
                      volume * result.executed_price, reason, profit_rate)
            await self.telegram.notify_sell(
                market, result.executed_price, volume,
                profit_rate, reason
            )

    async def _execute_sell(self, market: str, reason: str, current_price: float = None):
        # 이중 매도 방지
        if market in self._selling_markets:
            logger.debug(f"매도 중복 스킵 ({market})")
            return
        self._selling_markets.add(market)
        try:
            await self._execute_sell_inner(market, reason, current_price)
        finally:
            self._selling_markets.discard(market)

    async def _execute_sell_inner(self, market: str, reason: str, current_price: float = None):
        """전량 매도 실행"""
        # ── SmartWallet: 매도 수량 결정 ─────────────────────
        _symbol      = market.replace("KRW-", "")
        _confidence  = 1.0
        _sell_dec    = self._wallet.get_sell_decision(
            symbol        = _symbol,
            current_price = current_price,
            confidence    = _confidence,
        )
        # Paper 모드: SmartWallet 실잔고 체크 스킵
        if getattr(self.settings, 'paper_mode', True):
            pos = self.portfolio._positions.get(market)
            _wallet_sell_qty  = float(getattr(pos, 'quantity', 0)) if pos else 0.0
            _wallet_incl_dust = False
        else:
            if not _sell_dec['ok']:
                logger.warning(
                    f'⛔ SmartWallet 매도 보류 ({_symbol}): {_sell_dec["note"]}'
                )
                return
            _wallet_sell_qty  = _sell_dec['qty']
            _wallet_incl_dust = _sell_dec['includes_dust']
            logger.info(
                f'📤 SmartWallet 매도 결정 | {_symbol} | '
                f'수량={_wallet_sell_qty:.8f} | {_sell_dec["note"]}'
            )
        # ────────────────────────────────────────────────────
        pos = self.portfolio.get_position(market)
        if not pos:
            return

        req = ExecutionRequest(
            market       = market,
            side         = OrderSide.SELL,
            amount_krw   = 0,
            volume       = pos.volume,
            reason       = reason,
            strategy_name= getattr(pos, "strategy", "unknown") or "unknown",
        )

        result = await self.executor.execute(req)
        if result.executed_price > 0:
            proceeds, profit_rate = self.portfolio.close_position(
                market, result.executed_price, result.fee, reason
            )

            # ── DB SELL 저장 ──────────────────────────────────
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
                    "profit_rate": profit_rate / 100,  # ÷100: close_position 반환값은 퍼센트 단위
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
                    f"[DB-SELL] {market} profit={profit_rate/100:.4f} ({profit_rate:.2f}%) 저장 완료"
                )
            except Exception as _e:
                logger.warning(f"[DB-SELL] 저장 실패: {_e}")
            # ─────────────────────────────────────────────────

            self.trailing_stop.remove_position(market)
            self.partial_exit.remove_position(market)
            # 손절 후 재매수 쿨다운 등록 (4시간)
            if '손절' in reason or 'stop' in reason.lower():
                if not hasattr(self, '_sl_cooldown'):
                    self._sl_cooldown = {}
                import datetime as _dt
                self._sl_cooldown[market] = _dt.datetime.now() + _dt.timedelta(hours=4)
                logger.info(f'⏳ 손절 쿨다운 등록 ({market}): 4시간 재매수 금지')
                import datetime as _dt
                _cd_until = (_dt.datetime.now() + _dt.timedelta(hours=4)).isoformat()
                await self.db_manager.set_state(f'sl_cooldown_{market}', _cd_until)
            self.risk_manager.record_trade_result(profit_rate > 0)

            log_trade("SELL", market, result.executed_price,
                      proceeds, reason, profit_rate)
            await self.telegram.notify_sell(
                market, result.executed_price, result.executed_volume,
                profit_rate, reason
            )

        # ── SmartWallet: 매도 기록 ──────────────────────────
        try:
            _sold_qty = float(getattr(result, "executed_volume",
                        getattr(result, "quantity",
                        getattr(result, "qty", _wallet_sell_qty))))
            if _sold_qty > 0:
                self._wallet.record_sell(
                    symbol        = _symbol,
                    sold_qty      = _sold_qty,
                    includes_dust = _wallet_incl_dust,
                )
        except Exception as _we:
            logger.debug(f"SmartWallet record_sell 스킵: {_we}")
        # ────────────────────────────────────────────────────

    # ── 초기화 헬퍼 ──────────────────────────────────────────────

    def _apply_walk_forward_params(self):
        """
        ✅ Step 3: Walk-Forward 최적 파라미터 자동 로드
        config/optimized_params.json → 전략 파라미터 적용
        OOS 샤프 < 0.5 전략 → 자동 비활성화
        OOS 샤프 >= 1.5 전략 → 가중치 1.5배 부스트
        """
        try:
            from backtesting.walk_forward import WalkForwardRunner
            params = WalkForwardRunner.load_optimized_params()
            if not params:
                logger.info("Walk-Forward 파라미터 없음 → 기본값 사용")
                return

            applied = 0
            for strategy_name, info in params.items():
                if strategy_name not in self._strategies:
                    continue

                strategy = self._strategies[strategy_name]
                is_active = info.get("is_active", True)

                if not is_active:
                    strategy.disable()
                    logger.info(
                        f"  ❌ {strategy_name} 비활성화 "
                        f"(OOS 샤프={info.get('oos_sharpe', 0):.3f})"
                    )
                else:
                    # 파라미터 업데이트
                    if info.get("params"):
                        strategy.params.update(info["params"])

                    # 가중치 부스트 적용
                    weight_boost = info.get("weight_boost", 1.0)
                    if weight_boost != 1.0:
                        old_weight = self.signal_combiner.STRATEGY_WEIGHTS.get(
                            strategy_name, 1.0
                        )
                        new_weight = old_weight * weight_boost
                        self.signal_combiner.STRATEGY_WEIGHTS[strategy_name] = new_weight
                        logger.info(
                            f"  ⚡ {strategy_name} 가중치 {old_weight:.1f}"
                            f" → {new_weight:.1f} (boost={weight_boost}x)"
                        )
                    applied += 1

            logger.success(
                f"✅ Walk-Forward 파라미터 적용: {applied}개 전략"
            )
        except Exception as e:
            logger.warning(f"Walk-Forward 파라미터 로드 실패 (기본값 사용): {e}")

    def _load_strategies(self):
        """전략 플러그인 로드"""
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
        logger.info(f"✅ {len(self._strategies)}개 전략 로드 완료")

    async def _load_ml_model(self):
        """ML 앙상블 모델 GPU 로드 + torch.compile 최적화"""
        try:
            from models.inference.predictor import MLPredictor
            self._ml_predictor = MLPredictor()
            ok = await asyncio.get_event_loop().run_in_executor(
                None, self._ml_predictor.load_model
            )

            # ── torch.compile (PyTorch 2.0+ GPU only) ────────────
            if ok and self._device == "cuda" and self._ml_predictor._model is not None:
                # Windows: gpu_utils.maybe_compile 내부에서 자동 스킵됨
                # Linux: eager 모드로 compile 시도
                self._ml_predictor._model = maybe_compile(
                    self._ml_predictor._model,
                    backend="eager",
                    mode="default",
                )

            # ── GPU 메모리 상태 출력 ──────────────────────────────
            log_gpu_status()

            logger.success("✅ ML 앙상블 모델 로드 완료")
        except Exception as e:
            logger.warning(f"ML 모델 로드 실패 (전략 전용 모드로 실행): {e}")


    async def _market_scanner(self) -> list:
        """
        업비트 전체 KRW 마켓 스캐너
        - 30초마다 전체 종목 스캔
        - 거래량 급증 + 가격 급등 코인 동적 포착
        - 감시 풀에 추가 후 ML 분석 대상에 포함
        Returns: 새로 포착된 급등 코인 리스트
        """
        import time
        import asyncio

        cfg = self._SCANNER_CONFIG
        now = time.time()

        # 스캔 주기 확인
        if now - self._last_scan_time < cfg["interval_sec"]:
            return []
        self._last_scan_time = now

        try:
            # 업비트 전체 KRW 마켓 목록 조회
            all_markets = await self._get_all_krw_markets()
            if not all_markets:
                return []

            # 기존 고정 감시 코인 제외
            fixed_markets = set(self.markets) if hasattr(self, "markets") else set()
            exclude = set(cfg["exclude_markets"]) | fixed_markets
            scan_targets = [m for m in all_markets if m not in exclude]

            logger.debug(f"[Scanner] 전체 {len(scan_targets)}개 종목 스캔 시작")

            surge_candidates = []

            # 배치로 나눠서 스캔 (API 부하 방지)
            batch_size = 20
            for i in range(0, len(scan_targets), batch_size):
                batch = scan_targets[i:i + batch_size]
                tasks = [self._check_surge(m, cfg) for m in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for market, result in zip(batch, results):
                    if isinstance(result, Exception):
                        continue
                    if result and result.get("is_surge"):
                        surge_candidates.append(result)

                # 배치 간 딜레이 (API 보호)
                await asyncio.sleep(0.5)

            # 급등 점수 기준 정렬
            surge_candidates.sort(key=lambda x: x.get("score", 0), reverse=True)

            # 동적 감시 풀 업데이트
            new_markets = []
            current_dynamic = set(self._dynamic_markets)

            for candidate in surge_candidates[:cfg["max_dynamic_coins"]]:
                market = candidate["market"]
                if market not in current_dynamic:
                    self._dynamic_markets.append(market)
                    new_markets.append(market)
                    logger.info(
                        f"🚀 [Scanner] 급등 포착: {market} | "
                        f"거래량 급증={candidate['vol_ratio']:.1f}x | "
                        f"가격변화={candidate['price_change']:.2%} | "
                        f"거래대금={candidate['trade_amount']:,.0f}원"
                    )

            # 동적 풀 최대 크기 유지
            if len(self._dynamic_markets) > cfg["max_dynamic_coins"]:
                self._dynamic_markets = self._dynamic_markets[-cfg["max_dynamic_coins"]:]

            if new_markets:
                logger.info(f"[Scanner] 새 급등 코인 {len(new_markets)}개 감시 추가: {new_markets}")
            else:
                logger.debug(f"[Scanner] 새 급등 코인 없음")

            return new_markets

        except Exception as e:
            logger.warning(f"[Scanner] 스캔 오류: {e}")
            return []

    async def _get_all_krw_markets(self) -> list:
        """업비트 전체 KRW 마켓 목록 조회"""
        try:
            import aiohttp
            url = "https://api.upbit.com/v1/market/all"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params={"isDetails": "false"}) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
            return [
                item["market"] for item in data
                if item["market"].startswith("KRW-")
            ]
        except Exception as e:
            logger.warning(f"[Scanner] 마켓 목록 조회 오류: {e}")
            return []

    async def _check_surge(self, market: str, cfg: dict) -> dict:
        """
        단일 코인 급등 여부 확인
        - 1분봉 20개로 거래량 급증 + 가격 변화 판단
        """
        try:
            df = await self.rest_collector.get_ohlcv(market, "minute1", 25)
            if df is None or len(df) < 10:
                return {"is_surge": False}

            # 최근 1분 거래량
            recent_vol  = float(df["volume"].iloc[-1])
            recent_price = float(df["close"].iloc[-1])
            recent_amount = recent_vol * recent_price

            # 최소 거래대금 필터
            if recent_amount < cfg["min_trade_amount"]:
                return {"is_surge": False}

            # 20분 평균 거래량
            avg_vol = float(df["volume"].iloc[-21:-1].mean())
            if avg_vol <= 0:
                return {"is_surge": False}

            vol_ratio = recent_vol / avg_vol

            # 5분 가격 변화율
            price_5m_ago = float(df["close"].iloc[-6])
            price_change = (recent_price - price_5m_ago) / price_5m_ago

            # 급등 판단
            is_surge = (
                vol_ratio    >= cfg["vol_surge_ratio"] and
                price_change >= cfg["price_change_pct"]
            )

            if not is_surge:
                return {"is_surge": False}

            # 급등 점수 계산 (거래량 비율 x 가격 변화율)
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

        except Exception as e:
            return {"is_surge": False}

    async def _get_active_markets(self) -> list:
        """
        현재 감시 대상 전체 마켓 반환
        - 고정 코인 + 동적 스캐너 포착 코인
        """
        fixed  = list(self.markets) if hasattr(self, "markets") else []
        dynamic = [m for m in self._dynamic_markets if m not in fixed]
        return fixed + dynamic


    async def _run_backtest_v2(self, market: str,
                                interval: str = "minute60",
                                count: int = 500,
                                initial_capital: float = 1_000_000.0) -> dict:
        """
        백테스트 엔진 v2
        - 실거래 동일 조건: 슬리피지 + 수수료 + ATR v2 손절/익절
        - Kelly v2 포지션 사이징 적용
        - 결과: 수익률, 승률, Sharpe, MDD, 손익비
        """
        import numpy as np
        import pandas as pd

        FEE_RATE      = 0.0005
        SLIPPAGE_RATE = 0.0003

        try:
            df = await self.rest_collector.get_ohlcv(market, interval, count)
            if df is None or len(df) < 50:
                return {"error": "데이터 부족"}

            df = df.reset_index(drop=True)
            capital     = initial_capital
            position    = 0.0
            entry_price = 0.0
            stop_loss   = 0.0
            take_profit = 0.0
            trades      = []
            equity_curve = [capital]

            df["ema20"] = df["close"].ewm(span=20).mean()
            df["ema50"] = df["close"].ewm(span=50).mean()

            delta = df["close"].diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            df["rsi"] = 100 - 100 / (1 + gain / (loss + 1e-9))

            ema12          = df["close"].ewm(span=12).mean()
            ema26          = df["close"].ewm(span=26).mean()
            df["macd"]     = ema12 - ema26
            df["macd_sig"] = df["macd"].ewm(span=9).mean()

            close_prev = df["close"].shift(1)
            tr = pd.concat([
                df["high"] - df["low"],
                (df["high"] - close_prev).abs(),
                (df["low"]  - close_prev).abs(),
            ], axis=1).max(axis=1)
            df["atr"] = tr.rolling(14).mean()

            # 가격 기반 동적 프로필 (atr_stop.py의 _get_profile_by_price 사용)
            from risk.stop_loss.atr_stop import _get_profile_by_price
            _entry_est = float(df["close"].iloc[-1]) if len(df) > 0 else 1000
            _p = _get_profile_by_price(_entry_est)
            profile = {"atr_low": _p["min_sl"], "atr_high": _p["max_sl"]}

            for i in range(50, len(df)):
                row     = df.iloc[i]
                close   = float(row["close"])
                atr     = float(row["atr"]) if not pd.isna(row["atr"]) else close * 0.02
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
                            avg_l  = abs(sum(t["pnl_pct"] for t in losses) / max(len(losses), 1))
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
                position = 0.0

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

            returns = [t["pnl_pct"] for t in trades]
            sharpe  = (
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
                f"거래={total_trades} WR={win_rate:.1%} "
                f"PF={profit_factor:.2f} Return={total_return:.2%} "
                f"Sharpe={sharpe:.2f} MDD={mdd:.2%}"
            )
            return result

        except Exception as e:
            logger.warning(f"[Backtest v2] {market} 오류: {e}")
            return {"market": market, "error": str(e)}

    async def _run_backtest_all(self) -> dict:
        """전체 감시 코인 백테스트 일괄 실행 (매일 03:00 자동)"""
        import asyncio

        markets = list(self.markets) if hasattr(self, "markets") else []
        if not markets:
            return {}

        logger.info(f"[Backtest v2] 전체 백테스트 시작 | {len(markets)}개 코인")

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
                    line = (
                        market
                        + ": WR=" + str(round(result["win_rate"] * 100, 1)) + "%"
                        + " PF=" + str(result["profit_factor"])
                        + " Ret=" + str(round(result["total_return"] * 100, 1)) + "%"
                        + " MDD=" + str(round(result["mdd"] * 100, 1)) + "%"
                    )
                    lines.append(line)

        summary = " | ".join(lines)
        try:
            await self.telegram.send_message(summary)
        except Exception:
            pass

        logger.info(f"[Backtest v2] 완료 | {len(results)}개 결과")
        return results

    async def _init_ppo_agent(self):
        """
        PPO 강화학습 에이전트 자동 초기화

        동작 순서:
          1. 저장된 모델이 있으면 로드만
          2. 모델이 없으면 백그라운드로 자동 훈련 시작 (1일치 데이터 수집 후)
          3. 훈련 완료 후 신호 결합기에 자동 연결
        """
        try:
            from models.rl.ppo_agent import PPOTradingAgent, check_ppo_dependencies
            deps = check_ppo_dependencies()

            if not all(deps.values()):
                missing = [k for k, v in deps.items() if not v]
                logger.info(f"🤖 PPO 에이전트 대기 중 (미설치: {missing}) — 낙스뢰운 디폰데시 설치 후 활성화")
                return

            self._ppo_agent = PPOTradingAgent(use_gpu=(self._device == "cuda"))

            # 저장된 모델 존재 여부 확인
            loaded = self._ppo_agent.load_model()
            if loaded:
                logger.success("✅ PPO 모델 로드 완료 (저장된 비중 사용)")
            else:
                # 저장된 모델 없음 → 백그라운드 자동 훈련 예약
                logger.info("🤖 PPO 모델 없음 — 초기 데이터 수집 후 자동 훈련 시작")
                # 10분 후 백그라운드 훈련 시작 (엔진 시작 다음)
                from datetime import datetime, timedelta
                self.scheduler.add_job(
                    self._auto_train_ppo, "date",
                    run_date=datetime.now() + timedelta(minutes=10),
                    id="ppo_initial_train",
                )
                logger.info("🕒 PPO 자동 훈련: 엔진 시작 10분 후 시작 예약됨")

        except Exception as e:
            logger.warning(f"PPO 초기화 실패 (무시하고 계속): {e}")

    async def _auto_train_ppo(
        self,
        total_timesteps: int = 200_000,
        notify: bool = True,
    ):
        """
        PPO 자동 훈련 (백그라운드)

        엔진 호출 시점:
          - 엔진 시작 10분 후 (신규 시)
          - 매주 일요일 새벽 3시 (주간 재훈련)
        """
        logger.info("🤖 PPO 자동 훈련 시작 — 백그라운드 실행 중...")
        if notify:
            await self.telegram.send_message(
                "🤖 PPO 강화학습 훈련 시작\n"
                f"  대상 코인: {', '.join(self.settings.trading.target_markets)}\n"
                f"  에피소드: {total_timesteps:,}스텔\n"
                "  완료 시 텔레그램 알림 (약 15분 소요)"
            )

        try:
            from models.rl.ppo_agent import PPOTradingAgent
            from data.processors.candle_processor import CandleProcessor
            import pandas as pd

            markets = self.settings.trading.target_markets
            processor = CandleProcessor()

            # OHLCV 데이터 수집 (500개 캐들 = 약 3주일) - 429 방지: 순차
            logger.info("  훈련용 데이터 수집 중...")
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
                logger.warning("PPO 훈련 데이터 부족 — 훈련 취소")
                return

            # 웼타 데이터 합산
            combined_df = pd.concat(processed_dfs, ignore_index=True)
            logger.info(f"  훈련 데이터: {len(combined_df)}샘플 ({len(processed_dfs)}개 코인)")

            # 백그라운드로 실행
            agent = PPOTradingAgent(use_gpu=(self._device == "cuda"))
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: agent.train(combined_df, total_timesteps=total_timesteps)
            )

            if "error" not in result:
                self._ppo_agent = agent
                logger.success(
                    f"✅ PPO 자동 훈련 완료 | "
                    f"PnL={result.get('pnl_pct', 0):+.2f}% | "
                    f"승률={result.get('win_rate', 0):.1f}% | "
                    f"샵프={result.get('sharpe', 0):.3f}"
                )
                if notify:
                    await self.telegram.send_message(
                        f"✅ PPO 훈련 완료\n"
                        f"  PnL   : {result.get('pnl_pct', 0):+.2f}%\n"
                        f"  승률 : {result.get('win_rate', 0):.1f}%\n"
                        f"  샵프 : {result.get('sharpe', 0):.3f}\n"
                        f"  모델 : models/saved/ppo/ 저장됨\n"
                        f"  다음 재훈련: 매주 일요일 03:00"
                    )
                # 주간 재훈련 스케줄 등록
                self.scheduler.add_job(
                    lambda: asyncio.create_task(self._auto_train_ppo(total_timesteps)),
                    "cron",
                    day_of_week="mon", hour=3, minute=0,
                    id="ppo_weekly_retrain",
                    replace_existing=True,
                )
            else:
                logger.warning(f"PPO 훈련 실패: {result.get('error')}")

        except Exception as e:
            logger.error(f"PPO 자동 훈련 오류: {e}")

    async def _restore_positions_from_db(self):
        """재시작 시 DB에서 미청산 포지션 복원"""
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
                        logger.warning(f"포지션 복원 스킵 ({mkt}): 가격/수량 없음")
                        continue

                    self.portfolio.open_position(
                        market      = mkt,
                        entry_price = _price,
                        volume      = _volume,
                        amount_krw  = _amount_krw,
                        strategy    = _strategy,
                        stop_loss   = _price * 0.97,
                        take_profit = _price * 1.05,
                    )
                    self.trailing_stop.add_position(
                        market       = mkt,
                        entry_price  = _price,
                        initial_stop = _price * 0.97,
                        atr          = 0.0,
                    )
                    if self.position_mgr_v2 is not None:
                        try:
                            from risk.position_manager_v2 import PositionV2
                            _pv2 = PositionV2(
                                market      = mkt,
                                entry_price = _price,
                                volume      = _volume,
                                amount_krw  = _amount_krw,
                                stop_loss   = _price * 0.97,
                                take_profit = _price * 1.05,
                                strategy    = _strategy,
                            )
                            self.position_mgr_v2.add_position(_pv2)
                        except Exception as _rv2_e:
                            logger.debug(f"M4 복원 스킵: {_rv2_e}")
                    self.partial_exit.add_position(
                        market      = mkt,
                        entry_price = _price,
                        volume      = _volume,
                        take_profit = _price * 1.05,
                    )
                    self.adapter._paper_balance["KRW"] = max(
                        0.0,
                        self.adapter._paper_balance.get("KRW", 1_000_000) - _amount_krw
                    )
                    coin = mkt.replace("KRW-", "")
                    self.adapter._paper_balance[coin] = (
                        self.adapter._paper_balance.get(coin, 0.0) + _volume
                    )
                    restored += 1
                    total_invested += _amount_krw
                    logger.info(
                        f"♻️ 포지션 복원 | {mkt} | "
                        f"매수가={_price:,.0f} | "
                        f"금액=₩{_amount_krw:,.0f} | {_strategy}"
                    )
                except Exception as _row_e:
                    logger.warning(f"행 복원 스킵 ({row['market'] if row else '?'}): {_row_e}")
                    continue

            if restored:
                logger.info(
                    f"✅ 포지션 복원 완료: {restored}개 | "
                    f"투자금=₩{total_invested:,.0f}"
                )
                try:
                    _krw_cash = await self.adapter.get_balance("KRW")
                    _open_pos = {
                        m: {"volume": pos.volume}
                        for m, pos in self.portfolio.open_positions.items()
                    }
                    self.adapter.sync_paper_balance(_krw_cash, _open_pos)
                except Exception as _sync_e:
                    logger.debug(f"페이퍼 잔고 동기화 오류: {_sync_e}")
            else:
                logger.info("📭 복원할 포지션 없음 (신규 시작)")

            # BEAR_REVERSAL 오늘 카운터 복원
            try:
                from datetime import datetime as _dt_cls
                _today_str = _dt_cls.now().strftime("%Y-%m-%d")
                _bear_count_key = f"_bear_rev_count_{_today_str}"
                _bear_today = 0
                try:
                    import aiosqlite as _aio2
                    async with _aio2.connect(str(self.db_manager.db_path)) as _db2:
                        async with _db2.execute(
                            """
                            SELECT COUNT(*) FROM trade_history
                            WHERE strategy LIKE '%BEAR_REVERSAL%'
                              AND side = 'BUY'
                              AND DATE(timestamp) = DATE('now','localtime')
                            """
                        ) as _cur2:
                            _row2 = await _cur2.fetchone()
                            _bear_today = int(_row2[0]) if _row2 and _row2[0] is not None else 0
                except Exception:
                    _bear_today = 0
                setattr(self, _bear_count_key, _bear_today)
                _remain = max(0, 6 - _bear_today)
                _status = "⛔ 오늘 한도 초과" if _bear_today >= 6 else f"잔여 {_remain}회"
                logger.info(f"♻️  BEAR_REVERSAL 카운터 복원: 오늘 {_bear_today}회 → {_status}")
            except Exception as _br_e:
                logger.warning(f"⚠️ BEAR_REVERSAL 카운터 복원 실패: {_br_e}")

        except Exception as e:
            import traceback
            logger.warning(f"⚠️ 포지션 복원 실패 (무시): {e}")
            logger.debug(traceback.format_exc())

    async def _restore_sl_cooldown(self):
        """재시작 시 DB에서 손절 쿨다운 복원"""
        try:
            if not hasattr(self, '_sl_cooldown'):
                self._sl_cooldown = {}
            import datetime as _dt_cd
            # bot_state 테이블에서 sl_cooldown_ 으로 시작하는 모든 키 조회
            if self.db_manager._conn is not None:
                async with self.db_manager._lock:
                    async with self.db_manager._conn.execute(
                        """SELECT key, value FROM bot_state WHERE key LIKE 'sl_cooldown_%'"""
                    ) as _cur:
                        _rows = await _cur.fetchall()
                restored_count = 0
                now = _dt_cd.datetime.now()
                for _key, _val in _rows:
                    try:
                        _until = _dt_cd.datetime.fromisoformat(_val)
                        if _until > now:  # 아직 유효한 쿨다운만 복원
                            _mkt = _key.replace('sl_cooldown_', '', 1)
                            self._sl_cooldown[_mkt] = _until
                            _rem = int((_until - now).total_seconds() // 60)
                            logger.info(f'⏳ 쿨다운 복원 ({_mkt}): {_rem}분 남음')
                            restored_count += 1
                        else:  # 만료된 쿨다운은 DB에서 삭제
                            await self.db_manager.delete_state(_key)
                    except Exception as _e:
                        logger.debug(f'쿨다운 파싱 오류 [{_key}]: {_e}')
                if restored_count:
                    logger.info(f'✅ 손절 쿨다운 복원 완료: {restored_count}개 코인')
                else:
                    logger.info('📭 복원할 손절 쿨다운 없음')
        except Exception as _e:
            logger.warning(f'⚠️ 쿨다운 복원 실패 (무시): {_e}')

    async def _save_initial_candles(self):
        """초기 캔들 데이터를 NpyCache에 저장"""
        markets = self.settings.trading.target_markets
        saved = 0
        for market in markets:
            try:
                df = await self.rest_collector.get_ohlcv(market, interval='minute60', count=200)
                if df is not None and len(df) > 0:
                    self.cache_manager.set_ohlcv(market, '1h', df)
                    saved += 1
                    logger.debug(f'💾 캔들 저장 | {market} | {len(df)}개')
            except Exception as e:
                logger.debug(f'캔들 저장 실패 ({market}): {e}')
        logger.info(f'✅ 초기 캔들 NpyCache 저장 완료 | {saved}/{len(markets)}개 코인')

    async def _initial_data_fetch(self):
        """초기 OHLCV 데이터 수집"""
        logger.info("📥 초기 데이터 수집 중...")
        markets = self.settings.trading.target_markets
        tasks = [
            self.rest_collector.get_ohlcv(m, "minute60", 200)
            for m in markets
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success = sum(1 for r in results if r is not None and not isinstance(r, Exception))
        await self._save_initial_candles()
        logger.info(f"✅ 초기 데이터 수집 완료 ({success}/{len(markets)}개 성공)")

        # SmartWallet 초기 잔고 스캔
        try:
            raw_balances = await self.adapter.get_balances()
            if isinstance(raw_balances, list) and raw_balances:
                self._wallet.scan_balances(raw_balances)
            self._wallet.print_status()
        except Exception as e:
            logger.warning(f"SmartWallet 초기 스캔 실패: {e}")


    async def _run_auto_retrain(self):
        """AutoTrainer — 일일 자동 재학습 (스케줄러 03:00 호출)"""
        try:
            logger.info("[AutoTrainer] 🔄 일일 자동 재학습 시작...")
            result = await self.auto_trainer.run_if_needed()
            if result:
                await self._load_ml_model()
                logger.info("[AutoTrainer] ✅ 재학습 완료 + 새 모델 로드")
            else:
                logger.info("[AutoTrainer] ℹ️  재학습 스킵 또는 롤백")
        except Exception as e:
            logger.error(f"[AutoTrainer] 오류: {e}")

    def _register_schedules(self):
        """APScheduler 작업 등록"""
        from datetime import datetime, timedelta

        # 1분마다: 포트폴리오 가격 업데이트
        self.scheduler.add_job(
            self._scheduled_price_update, "interval", seconds=60, id="price_update"
        )
        # 1시간마다: 일봉 데이터 갱신
        self.scheduler.add_job(
            self._scheduled_daily_data, "interval", hours=1, id="daily_data"
        )
        # 매일 자정: 일일 성과 보고
        self.scheduler.add_job(
            self._scheduled_daily_report, "cron", hour=0, minute=0, id="daily_report"
        )
        # 24시간마다: ML 모델 재학습
        self.scheduler.add_job(
            self._scheduled_model_retrain, "interval",
            hours=self.settings.ml.retrain_interval_hours, id="retrain"
        )
        # 24시간마다: 페이퍼 트레이딩 성과 리포트 자동 생성
        first_run = datetime.now() + timedelta(hours=24)
        self.scheduler.add_job(
            self._scheduled_paper_report, "interval",
            hours=24, id="paper_report",
            next_run_time=first_run,
        )
        # ✅ 6시간마다: 김치 프리미엄 갱신
        self.scheduler.add_job(
            self._scheduled_kimchi_update, "interval",
            hours=6, id="kimchi_update"
        )
        # ✅ 1시간마다: 공포탐욕 지수 갱신
        self.scheduler.add_job(
            self._scheduled_fear_greed_update, "interval",
            hours=1, id="fear_greed_update"
        )
        # ✅ 매주 월요일 새벽 2시: Walk-Forward 자동 최적화
        self.scheduler.add_job(
            self._scheduled_walk_forward, "cron",
            day_of_week="mon", hour=2, minute=0, id="walk_forward"
        )
        # ✅ 30분마다: 뉴스 감성 갱신
        self.scheduler.add_job(
            self._scheduled_news_update, "interval",
            minutes=30, id="news_update"
        )
        # 1시간마다: 포지션 현황 텔레그램 전송
        self.scheduler.add_job(
            self._scheduled_position_summary, 'interval',
            hours=1, id='position_summary'
        )
        # 1시간마다: 성과 지표 체크 및 LiveReadiness
        self.scheduler.add_job(
            self._scheduled_performance_check, 'interval',
            hours=1, id='performance_check'
        )
        # ✅ 첫 실행 후 30분: Walk-Forward 최초 1회 실행 (params 취득 없으면)
        from pathlib import Path
        if not Path("config/optimized_params.json").exists():
            self.scheduler.add_job(
                self._scheduled_walk_forward, "date",
                run_date=datetime.now() + timedelta(minutes=30),
                id="walk_forward_initial",
            )
            logger.info("🕒 Walk-Forward 최초 실행: 30분 후 예약됨 (config/optimized_params.json 없음)")
        # ✅ Step 2: CUDA context 5분마다 유지 (RTX 5060 절전 방지)
        self.scheduler.add_job(
            lambda: __import__('utils.gpu_utils', fromlist=['warmup_keep_alive']).warmup_keep_alive(),
            'interval', minutes=5, id='cuda_keepalive'
        )
        # 1시간 텔레그램 자동 현황 요약
        self.scheduler.add_job(
            self.telegram.send_hourly_summary,
            'interval', hours=1,
            id='hourly_telegram_summary',
            name='1시간 텔레그램 요약',
            misfire_grace_time=60
        )
        # ✅ 매주 일요일 새벽 4시: PPO 온라인 재학습
        self.scheduler.add_job(
            self._scheduled_ppo_online_retrain, "cron",
            day_of_week="sun", hour=4, minute=0,
            id="ppo_online_retrain"
        )
        logger.info(f"✅ 스케줄러 등록 완료 ({len(self.scheduler.get_jobs())}개 작업)")

    # ── 스케줄된 작업 ────────────────────────────────────────────
    async def _scheduled_position_summary(self):
        """매 1시간마다 텔레그램으로 상세 포지션 현황 전송"""
        try:
            from datetime import datetime
            from monitoring.dashboard import dashboard_state
            positions = list(self.portfolio._positions.values()) if hasattr(self.portfolio, '_positions') else []
            if not positions:
                return
            now = datetime.now()
            lines = ["📊 <b>APEX BOT 포지션 현황</b>", f"🕐 {now.strftime('%m/%d %H:%M')} KST\n"]
            total_invested = 0.0
            total_eval = 0.0
            total_pnl_krw = 0.0
            win_count = 0
            for pos in positions:
                market  = getattr(pos, 'market', '?')
                entry   = float(getattr(pos, 'entry_price', 0) or 0)
                qty     = float(getattr(pos, 'quantity', 0) or 0)
                current = float(self.cache_manager.get_current_price(market) or entry)
                invested = entry * qty
                eval_val = current * qty
                pnl_pct  = (current - entry) / entry * 100 if entry else 0
                pnl_krw  = eval_val - invested
                total_invested += invested
                total_eval += eval_val
                total_pnl_krw += pnl_krw
                if pnl_pct >= 0:
                    win_count += 1
                entry_time = getattr(pos, 'entry_time', None)
                try:
                    hold_h = (now - entry_time).total_seconds() / 3600 if entry_time else 0
                    hold_str = f"{hold_h:.1f}h"
                except Exception:
                    hold_str = "?"
                sl_pct  = float(getattr(pos, 'stop_loss_pct',  -3.0) or -3.0)
                tp_pct  = float(getattr(pos, 'take_profit_pct', 5.0) or  5.0)
                sl_dist = sl_pct - pnl_pct
                tp_dist = tp_pct - pnl_pct
                ml_info = dashboard_state.signals.get('ml_predictions', {}).get(market, {})
                ml_sig  = ml_info.get('signal', '-')
                ml_conf = float(ml_info.get('confidence', 0))
                ml_icon = {'BUY': '🟢', 'SELL': '🔴', 'HOLD': '🟡'}.get(ml_sig, '⚪')
                coin = market.replace('KRW-', '')
                pnl_icon = '🟢' if pnl_pct >= 0 else '🔴'
                lines.append(f"{pnl_icon} <b>{coin}</b>  {pnl_pct:+.2f}% ({pnl_krw:+,.0f}원)")
                lines.append(f"   진입 {entry:,.0f} → 현재 {current:,.0f}  보유 {hold_str}")
                lines.append(f"   SL까지 {sl_dist:+.1f}%  TP까지 {tp_dist:+.1f}%")
                lines.append(f"   ML {ml_icon}{ml_sig}({ml_conf:.0%})  수량 {qty:.4f}\n")
            total_pnl_pct = (total_eval - total_invested) / total_invested * 100 if total_invested else 0
            cash = float(getattr(self.portfolio, 'cash', 0) or 0)
            total_assets = total_eval + cash
            lines.append("─────────────────────")
            lines.append(f"💰 총 평가금액: <b>{total_assets:,.0f}원</b>")
            lines.append(f"📈 포지션 손익: <b>{total_pnl_pct:+.2f}%</b>  ({total_pnl_krw:+,.0f}원)")
            lines.append(f"💵 현금 잔고:   {cash:,.0f}원")
            lines.append(f"🏆 수익 포지션: {win_count}/{len(positions)}개")
            fg = getattr(self, '_fear_greed_index', None)
            if fg is not None:
                fg_label = '극단적 공포' if fg < 25 else ('공포' if fg < 45 else ('중립' if fg < 55 else ('탐욕' if fg < 75 else '극단적 탐욕')))
                lines.append(f"\n😨 공포탐욕: {fg}  ({fg_label})")
            btc_status = self.correlation_filter.get_btc_status() if hasattr(self, 'correlation_filter') else {}
            if btc_status.get('trend') == 'DOWN':
                lines.append("⚠️ BTC 하락세 감지 - 신규 매수 차단 중")
            news_sig = dashboard_state.signals.get('news_sentiment', {})
            if news_sig.get('overall_sentiment') in ('BEARISH', 'VERY_BEARISH'):
                lines.append(f"📰 뉴스 감성: {news_sig.get('overall_sentiment')} ⚠️")
            await self.telegram.send_message("\n".join(lines))
        except Exception as e:
            logger.debug(f"포지션 요약 전송 오류: {e}")

    async def _scheduled_performance_check(self):
        """매 1시간 성과 지표 계산 및 LiveReadiness 체크"""
        try:
            trades = await self.db_manager.get_trades(limit=50)
            if not trades:
                return
            await self.performance_tracker.update(trades)
            metrics = self.performance_tracker.get_metrics()
            score = await self.live_readiness.check(self.performance_tracker)
            logger.info(
                f"성과 지표: win_rate={metrics.get('win_rate',0):.1%} "
                f"sharpe={metrics.get('sharpe_ratio',0):.2f} "
                f"mdd={metrics.get('max_drawdown',0):.1%} "
                f"live_score={score:.0f}/100"
            )
            if score >= 70:
                logger.info("LiveReadiness 70점 이상 - Live 전환 검토 가능")
            elif score < 30 and len(trades) > 20:
                await self.telegram.send_alert('WARNING',
                    f'LiveReadiness 점수 {score:.0f}/100 - 전략 점검 필요')
        except Exception as e:
            logger.debug(f"성과 체크 오류: {e}")

    async def _scheduled_price_update(self):
        """주기적 가격 업데이트"""
        pass  # ws_collector가 실시간으로 처리

    async def _scheduled_daily_data(self):
        """일봉 데이터 갱신"""
        for market in self.settings.trading.target_markets:
            try:
                df = await self.rest_collector.get_ohlcv(market, "day", 200)
                if df is not None:
                    await self.candle_processor.process(market, df, "1440")
            except Exception as e:
                logger.error(f"일봉 갱신 오류 ({market}): {e}")

    async def _scheduled_daily_report(self):
        """일일 성과 보고"""
        stats = self.portfolio.get_statistics()
        krw = await self.adapter.get_balance("KRW")
        total = self.portfolio.get_total_value(krw)
        daily_pnl = self.portfolio.get_daily_pnl(total)

        report = {
            **stats,
            "date": now_kst().strftime("%Y-%m-%d"),
            "daily_pnl": daily_pnl,
            "total_assets": total,
            "open_positions": self.portfolio.position_count,
        }
        await self.telegram.send_daily_report(report)
        # DB daily_performance 저장
        try:
            await self.db_manager.save_daily_performance({
                'date':            report.get('date'),
                'total_assets':    report.get('total_assets', 0),
                'daily_pnl':       report.get('daily_pnl', 0),
                'open_positions':  report.get('open_positions', 0),
                'win_rate':        report.get('win_rate', 0),
                'trade_count':     report.get('trade_count', 0),
            })
            logger.info('✅ daily_performance DB 저장 완료')
        except Exception as _dpe:
            logger.debug(f'daily_performance 저장 실패: {_dpe}')

    async def _scheduled_model_retrain(self):
        """ML 모델 재학습"""
        if self._ml_predictor:
            logger.info("🔄 ML 모델 재학습 시작...")
            # ML 모델 성능 지표 DB 저장
            try:
                from datetime import datetime
                await self.db_manager.save_model_metrics({
                    'timestamp':  datetime.now().isoformat(),
                    'model_name': 'ensemble',
                    'val_acc':    getattr(self._ml_predictor, '_last_val_acc', 0.0),
                    'train_loss': getattr(self._ml_predictor, '_last_train_loss', 0.0),
                    'val_loss':   getattr(self._ml_predictor, '_last_val_loss', 0.0),
                    'parameters': 12299965,
                })
                logger.info('✅ model_metrics DB 저장 완료')
            except Exception as _mme:
                logger.debug(f'model_metrics 저장 실패: {_mme}')
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._ml_predictor.retrain
                )
                logger.info("✅ ML 모델 재학습 완료")
            except Exception as e:
                logger.error(f"재학습 실패: {e}")


    async def _scheduled_ppo_online_retrain(self):
        """PPO 온라인 재학습 (매주 일요일 04:00)"""
        try:
            if not hasattr(self, 'ppo_online_trainer'):
                return
            stats = self.ppo_online_trainer.get_buffer_stats()
            logger.info(
                f"[PPOOnline] 주간 재학습 시작 | "
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
                logger.info("[PPOOnline] ✅ 주간 재학습 완료 + 모델 재로드")
            else:
                logger.info("[PPOOnline] ℹ️  재학습 스킵 (경험 부족 또는 실패)")
        except Exception as e:
            logger.error(f"[PPOOnline] 스케줄 오류: {e}")

    async def _scheduled_paper_report(self, hours: int = 24):
        """페이퍼 트레이딩 24시간 성과 리포트 자동 생성"""
        logger.info(f"📊 {hours}시간 페이퍼 트레이딩 리포트 생성 중...")
        try:
            data = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: generate_paper_report(
                    hours=hours,
                    output_dir="reports/paper",
                )
            )
            m = data.get("metrics", {})
            pnl = m.get("total_pnl_pct", 0)
            sign = "+" if pnl >= 0 else ""

            # ✅ 공포탐욕 지수 포함
            fg_line = ""
            if self.fear_greed.is_valid:  # ✅ FIX: property
                fg_line = f"공포탐욕: {self.fear_greed.index} ({self.fear_greed.label})\n"

            # ✅ 상관관계 상태 포함
            btc_status = self.correlation_filter.get_btc_status()
            btc_line = ""
            if btc_status.get("is_globally_blocked"):
                btc_line = f"⚠️ BTC 급락 차단 중 ({btc_status['block_remaining_sec']}초 남음)\n"

            msg = (
                f"📊 [{hours}시간 리포트]\n"
                f"수익률 : {sign}{pnl:.2f}%\n"
                f"승률   : {m.get('win_rate', 0):.1f}%\n"
                f"거래수 : {m.get('total_trades', 0)}회\n"
                f"샤프   : {m.get('sharpe_ratio', 0):.3f}\n"
                f"최대DD : -{m.get('max_drawdown_pct', 0):.2f}%\n"
                f"{fg_line}"
                f"{btc_line}"
                f"리포트 : reports/paper/ 폴더 확인"
            )
            await self.telegram.send_message(msg)
            logger.success("✅ 페이퍼 리포트 생성 완료")
        except Exception as e:
            logger.error(f"페이퍼 리포트 생성 실패: {e}")

    async def _scheduled_kimchi_update(self):
        """김치 프리미엄 주기 갱신"""
        try:
            await self.kimchi_monitor.fetch_all()
            summary = self.kimchi_monitor.get_summary()
            # ── dashboard_state 갱신 ──
            try:
                from monitoring.dashboard import dashboard_state
                premium_val = summary.get("premium_pct") if isinstance(summary, dict) else None
                if premium_val is None and hasattr(self.kimchi_monitor, "premium_pct"):
                    premium_val = self.kimchi_monitor.premium_pct
                dashboard_state.signals["kimchi_premium"] = premium_val
            except Exception:
                pass
            logger.info(f"🌶️ 김치 프리미엄 갱신: {summary}")
        except Exception as e:
            logger.warning(f"김치 프리미엄 갱신 실패: {e}")

    async def _scheduled_fear_greed_update(self):
        """공포탐욕 지수 주기 갱신"""
        try:
            ok = await self.fear_greed.fetch()
            if ok:
                logger.info(
                    f"😱 공포탐욕 지수 갱신: {self.fear_greed.index} "
                    f"({self.fear_greed.label})"
                )
                # 극단 수치일 때 텔레그램 알림
                idx = self.fear_greed.index or 50
                if idx <= 15:
                    await self.telegram.send_message(
                        f"⚠️ 공포탐욕: 극도 공포 {idx} — 역발상 매수 기회 탐색 중"
                    )
                elif idx >= 85:
                    await self.telegram.send_message(
                        f"⚠️ 공포탐욕: 극도 탐욕 {idx} — 신규 매수 억제 모드"
                    )
        except Exception as e:
            logger.warning(f"공포탐욕 지수 갱신 실패: {e}")

    async def _scheduled_walk_forward(self):
        """매주 Walk-Forward 자동 최적화 → 최적 파라미터 적용"""
        logger.info("🔬 주간 Walk-Forward 최적화 시작...")
        try:
            from backtesting.walk_forward import run_weekly_walk_forward
            results = await run_weekly_walk_forward()
            profitable = [k for k, v in results.items() if v.is_profitable]
            msg = (
                f"🔬 Walk-Forward 완료\n"
                f"수익 전략: {', '.join(profitable) if profitable else '없음'}\n"
                f"최적 파라미터 → config/optimized_params.json 저장"
            )
            await self.telegram.send_message(msg)
        except Exception as e:
            logger.error(f"Walk-Forward 스케줄 실패: {e}")

    async def _scheduled_news_update(self):
        """뉴스 감성 30분 주기 갱신"""
        try:
            count = await self.news_analyzer.fetch_news()
            logger.debug(f"뉴스 갱신 완료: {count}건")
        except Exception as e:
            logger.debug(f"뉴스 갱신 실패: {e}")


    async def _ws_reconnect_loop(self):
        """WebSocket 재연결 루프 (네트워크 끊김 대비)"""
        import asyncio
        RECONNECT_DELAY = 5   # 초
        MAX_DELAY       = 60  # 최대 대기
        delay = RECONNECT_DELAY
        while True:
            try:
                if self.ws_collector and not self.ws_collector.is_connected():
                    logger.warning(f"⚠️ WebSocket 연결 끊김 → {delay}초 후 재연결 시도")
                    await asyncio.sleep(delay)
                    await self.ws_collector.reconnect()
                    logger.info("✅ WebSocket 재연결 성공")
                    delay = RECONNECT_DELAY
                else:
                    delay = RECONNECT_DELAY
                    await asyncio.sleep(10)
            except Exception as e:
                logger.error(f"❌ WebSocket 재연결 실패: {e}")
                delay = min(delay * 2, MAX_DELAY)
                await asyncio.sleep(delay)

    async def _update_dashboard_state(self, krw: float, total_value: float):
        """대시보드 상태 직접 갱신 (broadcast는 2초 루프가 담당)"""
        from monitoring.dashboard import dashboard_state
        try:
            stats     = self.portfolio.get_statistics()
            daily_pnl = self.portfolio.get_daily_pnl(total_value)
            drawdown  = self.portfolio.get_current_drawdown(total_value)
            # ── 외부 데이터 dashboard_state 갱신 ──────────
            try:
                # 김치프리미엄
                _ks = self.kimchi_monitor.get_summary()
                _kv = (
                    _ks.get("premium_pct") if isinstance(_ks, dict)
                    else getattr(self.kimchi_monitor, "premium_pct", None)
                )
                if _kv is not None:
                    dashboard_state.signals["kimchi_premium"] = _kv
                # 뉴스 감성
                _ns = self.news_analyzer.get_dashboard_summary()
                _gs = _ns.get("global_sentiment", None)
                if _gs is not None:
                    _nl = "Positive" if _gs >= 0.2 else ("Negative" if _gs <= -0.2 else "Neutral")
                    dashboard_state.signals["news_sentiment"] = _nl
                    dashboard_state.signals["news_score"]     = round(float(_gs), 3)
                # 시장 국면 (Fear & Greed 기반 간이 판단)
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
                # signals 딕셔너리에 None이나 "--" 남아있는 키 정리
                for _k in list(dashboard_state.signals.keys()):
                    if dashboard_state.signals[_k] in (None, "--", ""):
                        if _k not in ("market_regime",):
                            pass  # 유효하지 않은 값은 삭제하지 않고 유지
            except Exception:
                pass

            # ── positions_detail 배열 ──────────────────────────────
            # ── portfolio 전체 갱신 ──────────────────────
            _pos_dict = {}
            for _m, _pos in self.portfolio.open_positions.items():
                _cp = getattr(_pos, "current_price", None) or _pos.entry_price
                _pnl = (_cp - _pos.entry_price) / _pos.entry_price * 100
                _pos_dict[_m] = {
                    "entry_price":      _pos.entry_price,
                    "current_price":    _cp,
                    "volume":           _pos.volume,
                    "unrealized_pnl_pct": round(_pnl, 2),
                    "hold_hours":       0.0,
                    "strategy":         getattr(_pos, "strategy", "-"),
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
                    "stop_loss":     getattr(pos, "stop_loss", None),
                })
            invested_total = sum(p["amount_krw"] for p in positions_detail)

            # ── 포트폴리오 ──────────────────────────────────────────
            dashboard_state.portfolio.update({
                "total_assets":     round(total_value, 0),
                "cash":             round(krw, 0),
                "invested":         round(invested_total, 0),
                "positions":        len(positions_detail),
                "positions_detail": positions_detail,
                "mode":             "PAPER" if getattr(self, "mode", "paper") == "paper" else "LIVE",
                "pnl":              round(daily_pnl, 0),
            })

            # ── 메트릭스 ────────────────────────────────────────────
            dashboard_state.metrics.update({
                "daily_pnl":     daily_pnl,
                "total_trades":  stats.get("total_trades", 0),
                "win_rate":      stats.get("win_rate", 0),
                "profit_factor": stats.get("profit_factor", 0),
                "max_drawdown":  drawdown,
                "sharpe_ratio":  stats.get("sharpe_ratio", 0),
                "strategy_stats": stats.get("strategy_stats", []),
            })

            # ── 시그널/감성 ─────────────────────────────────────────
            # ── 김치 프리미엄 ──────────────────────────────────────
            kimchi_pct = None
            try:
                premiums = self.kimchi_monitor.get_all_premiums()
                if premiums:
                    vals = [v for v in premiums.values() if v is not None]
                    kimchi_pct = round(sum(vals) / len(vals), 2) if vals else None
            except Exception:
                pass

            bear_count = getattr(self, "_bear_reversal_today", 0)
            btc_status = self.correlation_filter.get_btc_status()

            # ── 뉴스 감성 ────────────────────────────────────────────
            news_label = "--"
            try:
                ns = self.news_analyzer.get_dashboard_summary()
                score = ns.get("global_sentiment", 0.0)
                if score > 0.3:
                    news_label = "긍정적"
                elif score < -0.3:
                    news_label = "부정적"
                else:
                    news_label = "중립"
            except Exception:
                pass

            # ── 시장 국면 ────────────────────────────────────────────
            last_regime = "--"
            try:
                last_regime = getattr(self, "_last_regime", "--")
                if last_regime == "--":
                    # TrendFilter에서 마지막 레짐 읽기
                    tf = getattr(self, "trend_filter", None)
                    if tf and hasattr(tf, "get_regime"):
                        from data.storage.cache_manager import CacheManager
                        # 캐시에서 BTC 일봉 데이터로 레짐 판단
                        btc_df = self._market_data_cache.get("KRW-BTC-1d")
                        if btc_df is not None and len(btc_df) > 20:
                            last_regime = tf.get_regime(btc_df)
            except Exception:
                pass

            dashboard_state.signals.update({
                "fear_greed":         self.fear_greed.index,
                "fear_greed_label":   self.fear_greed.label,
                "kimchi_premium":     kimchi_pct,
                "news_sentiment":     news_label,
                "market_regime":      last_regime,
                "bear_reversal_count": bear_count,
                "btc_shock_blocked":  btc_status.get("is_globally_blocked", False),
            })

        except Exception as _e:
            logger.debug(f"대시보드 업데이트 오류: {_e}")

