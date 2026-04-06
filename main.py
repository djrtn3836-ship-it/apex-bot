"""
APEX BOT - 메인 진입점
업비트 전용 AI 퀀트 자동매매봇

사용법:
  python main.py                    # 기본 (paper 모드)
  python main.py --mode live        # 실거래
  python main.py --mode backtest    # 백테스트
  python main.py --mode paper       # 페이퍼 트레이딩
  python main.py --setup            # 초기 설정
"""
import asyncio
import argparse
import sys
import os
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
sys.path.insert(0, str(Path(__file__).parent))

from loguru import logger


def parse_args():
    parser = argparse.ArgumentParser(
        description="APEX BOT - Upbit AI Quant Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python main.py --mode paper       # 모의거래 (추천: 처음 실행 시)
  python main.py --mode live        # 실거래 (API 키 필요)
  python main.py --mode backtest    # 백테스트
  python main.py --setup            # .env 파일 초기화
        """
    )
    parser.add_argument("--mode", choices=["live", "paper", "backtest", "report", "walk-forward", "ppo-train", "news-check"],
                        default="paper", help="운영 모드")
    parser.add_argument("--hours", type=int, default=24,
                        help="리포트 분석 기간 (시간, 기본 24)")
    parser.add_argument("--setup", action="store_true", help="초기 설정")
    parser.add_argument("--debug", action="store_true", help="디버그 모드")
    parser.add_argument("--market", default=None, help="단일 마켓 백테스트")
    parser.add_argument("--days", type=int, default=90, help="백테스트 기간 (일)")
    parser.add_argument("--gpu-check", action="store_true",
                        help="GPU 상태 진단 및 PyTorch 설치 안내")
    parser.add_argument("--ppo-steps", type=int, default=100_000,
                        help="PPO 훈련 타임스텔 (기본 100000)")
    return parser.parse_args()


def _run_gpu_check():
    """창보에 GPU 상태 보고"""
    from utils.gpu_utils import (
        setup_gpu, get_gpu_memory_info,
        get_torch_install_cmd, log_gpu_status,
    )
    sep = "=" * 60
    print(f"\n{sep}")
    print("  ⚡ APEX BOT - GPU 진단")
    print(sep)

    try:
        import torch
        print(f"  PyTorch  : {torch.__version__}")
        print(f"  CUDA     : {torch.version.cuda}")
        cuda_ok = torch.cuda.is_available()
        print(f"  CUDA 사용 : {'✅ 가능' if cuda_ok else '❌ 불가'}")

        if cuda_ok:
            props = torch.cuda.get_device_properties(0)
            print(f"  GPU명   : {props.name}")
            print(f"  VRAM    : {props.total_memory / 1e9:.1f} GB")
            print(f"  SM      : {props.major}.{props.minor}")
            print(f"  멀티GPU  : {torch.cuda.device_count()}대")
            device = setup_gpu(use_gpu=True, benchmark=True, tf32=True)
            log_gpu_status()
            cmd = get_torch_install_cmd(props.name)
            print(f"\n  최적 PyTorch 설치 명령어:")
            print(f"  {cmd}")
        else:
            print("\n  CPU 모드 - GPU 가속 없이 동작")
            print("  NVIDIA GPU가 있다면: https://developer.nvidia.com/cuda-downloads")
    except ImportError:
        print("  ❌ PyTorch 미설치 - install_windows.bat 실행 후 재시도")

    print(sep + "\n")


def setup_initial():
    """초기 설정 안내"""
    env_example = Path(".env.example")
    env_file = Path(".env")

    print("\n" + "="*60)
    print("  APEX BOT 초기 설정")
    print("="*60)

    if env_file.exists():
        print("⚠️  .env 파일이 이미 존재합니다.")
    elif env_example.exists():
        import shutil
        shutil.copy(env_example, env_file)
        print("✅ .env 파일 생성 완료")
        print("\n📝 .env 파일을 열어 다음 값을 설정하세요:")
        print("   UPBIT_ACCESS_KEY=<업비트 API 접근키>")
        print("   UPBIT_SECRET_KEY=<업비트 API 시크릿키>")
        print("   TELEGRAM_TOKEN=<텔레그램 봇 토큰> (선택)")
        print("   TELEGRAM_CHAT_ID=<텔레그램 채팅 ID> (선택)")
    else:
        print("❌ .env.example 파일을 찾을 수 없습니다.")

    print("\n📦 패키지 설치:")
    print("   pip install -r requirements.txt")
    print("\n🚀 시작:")
    print("   python main.py --mode paper  (모의거래 먼저 테스트)")
    print("="*60 + "\n")


async def run_paper_trading():
    """페이퍼 트레이딩 실행"""
    # 환경변수 강제로 paper 모드 설정
    os.environ["TRADING_MODE"] = "paper"

    from config.settings import Settings, APIConfig, TradingConfig, RiskConfig, MLConfig, MonitoringConfig, DatabaseConfig
    import config.settings as settings_module

    # 페이퍼 모드용 설정 (API 키 없어도 작동)
    settings = Settings(mode="paper")
    settings_module._settings = settings

    from core.engine import TradingEngine
    engine = TradingEngine()
    await engine.start()


async def run_live_trading():
    """실거래 실행"""
    # .env에서 API 키 확인
    if not os.getenv("UPBIT_ACCESS_KEY"):
        logger.error("❌ UPBIT_ACCESS_KEY 환경변수가 없습니다")
        logger.error("   .env 파일에 API 키를 설정하세요 (python main.py --setup)")
        sys.exit(1)

    os.environ["TRADING_MODE"] = "live"
    from core.engine import TradingEngine
    engine = TradingEngine()

    # 실거래 확인
    print("\n" + "⚠️ " * 20)
    print("  경고: 실거래 모드입니다!")
    print("  실제 자금으로 거래가 실행됩니다.")
    print("⚠️ " * 20)
    confirm = input("\n계속하시겠습니까? (yes 입력): ")
    if confirm.lower() != "yes":
        print("취소됨.")
        sys.exit(0)

    await engine.start()


async def run_backtest(market: str = None, days: int = 90):
    """백테스트 실행"""
    from backtesting.backtester import Backtester
    from data.collectors.rest_collector import RestCollector
    from data.processors.candle_processor import CandleProcessor
    from strategies.momentum.macd_cross import MACDCrossStrategy
    from strategies.momentum.rsi_divergence import RSIDivergenceStrategy
    from strategies.mean_reversion.bollinger_squeeze import BollingerSqueezeStrategy
    from signals.signal_combiner import SignalCombiner
    from config.settings import get_settings
    import pandas as pd

    settings = get_settings()
    markets = [market] if market else settings.trading.target_markets[:3]

    logger.info(f"🔬 백테스트 시작 | {days}일 | {len(markets)}개 코인")

    collector = RestCollector()
    processor = CandleProcessor()
    backtester = Backtester()

    # 전략 신호 생성 함수
    strategy = MACDCrossStrategy()
    rsi_strategy = RSIDivergenceStrategy()
    bb_strategy = BollingerSqueezeStrategy()

    def combined_signal_fn(df: pd.DataFrame) -> pd.Series:
        """백테스트용 신호 함수"""
        signals = pd.Series(0, index=df.index)
        for i in range(len(df)):
            sub_df = df.iloc[:i+1]
            if len(sub_df) < 30:
                continue
            sig1 = strategy.analyze("BT", sub_df)
            sig2 = rsi_strategy.analyze("BT", sub_df)
            sig3 = bb_strategy.analyze("BT", sub_df)

            score = 0
            for s in [sig1, sig2, sig3]:
                if s is None:
                    continue
                from strategies.base_strategy import SignalType
                if s.signal_type == SignalType.BUY:
                    score += s.strength * s.confidence
                elif s.signal_type == SignalType.SELL:
                    score -= s.strength * s.confidence

            if score > 0.5:
                signals.iloc[i] = 1
            elif score < -0.5:
                signals.iloc[i] = -1
        return signals

    print("\n" + "="*60)
    all_results = []
    for mkt in markets:
        print(f"\n📊 백테스트: {mkt}")
        candles = days * 24  # 1시간봉 기준
        df = await collector.get_ohlcv(mkt, "minute60", min(candles, 2000))
        if df is None or len(df) < 50:
            print(f"  ⚠️ 데이터 부족: {mkt}")
            continue

        processed = await processor.process(mkt, df, "60")
        if processed is None:
            continue

        result = await backtester.run(processed, combined_signal_fn, mkt)
        all_results.append(result)

        summary = result.summary()
        for k, v in summary.items():
            print(f"  {k}: {v}")

    # Walk-Forward Analysis
    if all_results:
        print("\n" + "="*60)
        print("📈 Walk-Forward Analysis")
        for mkt in markets[:1]:  # 첫 번째 마켓만
            df = await collector.get_ohlcv(mkt, "minute60", 2000)
            if df is not None:
                processed = await processor.process(mkt, df, "60")
                if processed is not None:
                    wf_results = await backtester.walk_forward(
                        processed, combined_signal_fn, mkt, n_splits=5
                    )
                    wf_returns = [r.total_return for r in wf_results]
                    print(f"\n  {mkt} Walk-Forward 결과:")
                    print(f"  평균 수익률: {sum(wf_returns)/len(wf_returns):.2f}%")
                    print(f"  최고: {max(wf_returns):.2f}%")
                    print(f"  최저: {min(wf_returns):.2f}%")

    print("\n✅ 백테스트 완료")
    print("="*60)


def main():
    """메인 함수"""
    args = parse_args()

    # GPU 진단
    if getattr(args, "gpu_check", False):
        _run_gpu_check()
        return

    # 초기 설정
    if args.setup:
        setup_initial()
        return

    # .env 로드
    from dotenv import load_dotenv
    load_dotenv()

    # 디버그 모드
    if args.debug:
        os.environ["LOG_LEVEL"] = "DEBUG"

    print(f"""
╔══════════════════════════════════════════════════╗
║          ⚡  APEX BOT  v1.0.0  ⚡              ║
║     Upbit AI Quant Auto Trading System           ║
║     모드: {args.mode.upper():<40}║
╚══════════════════════════════════════════════════╝
    """)

    # 실행 모드 선택
    try:
        if args.mode == "paper":
            asyncio.run(run_paper_trading())
        elif args.mode == "live":
            asyncio.run(run_live_trading())
        elif args.mode == "backtest":
            asyncio.run(run_backtest(args.market, args.days))
        elif args.mode == "report":
            # report 모드: API 키 없이 DB만 읽어 리포트 생성
            import config.settings as settings_module
            settings_module._settings = Settings(mode="paper")
            from monitoring.paper_report import generate_paper_report
            hours = getattr(args, "hours", 24)
            print(f"\n📊 페이퍼 트레이딩 {hours}시간 리포트 생성 중...")
            result = generate_paper_report(hours=hours, output_dir="reports/paper")
            m = result.get("metrics", {})
            pnl = m.get('total_pnl_pct', 0)
            sign = '+' if pnl >= 0 else ''
            print(f"\n{'='*50}")
            print(f"  수익률 : {sign}{pnl:.2f}%  |  "
                  f"승률 : {m.get('win_rate',0):.1f}%  |  "
                  f"거래수 : {m.get('total_trades',0)}회")
            print(f"  샤프   : {m.get('sharpe_ratio',0):.3f}  |  "
                  f"최대DD : -{m.get('max_drawdown_pct',0):.2f}%")
            print(f"{'='*50}")
            print("\n✅ 완료! reports/paper/ 폴더에서 HTML 파일을 여세요.")
        
        elif args.mode == "walk-forward":
            # Walk-Forward 자동 최적화 모드
            import config.settings as settings_module
            settings_module._settings = Settings(mode="paper")
            from backtesting.walk_forward import WalkForwardRunner
            print("\n🔬 Walk-Forward 파라미터 최적화 시작...")
            
            async def run_wf():
                runner = WalkForwardRunner(
                    in_sample_days=90, out_sample_days=30, n_trials=50
                )
                results = await runner.run_all_strategies()
                runner.apply_best_params(results)
                print("\n=== Walk-Forward 최적화 결과 ===")
                for strat, r in results.items():
                    print(
                        f"  {strat:20s} | 샤프={r.oos_sharpe:+.3f} | "
                        f"승률={r.oos_win_rate:.1f}% | PnL={r.oos_pnl_pct:+.2f}% | "
                        f"{'✅ 적용' if r.is_profitable else '❌ 제외'}"
                    )
                print("\n✅ 완료! reports/walk_forward/ 폴더에서 HTML 파일을 여세요.")
            asyncio.run(run_wf())
        
        elif args.mode == "ppo-train":
            # PPO 강화학습 훈련 모드
            import config.settings as settings_module
            settings_module._settings = Settings(mode="paper")
            from models.rl.ppo_agent import PPOTradingAgent, check_ppo_dependencies
            deps = check_ppo_dependencies()
            missing = [k for k, v in deps.items() if not v]
            if missing:
                print(f"\n❌ PPO 의존성 미설치: {missing}")
                print("설치: pip install gymnasium stable-baselines3 torch")
            else:
                print(f"\n🤖 PPO 강화학습 훈련 시작 (스텝: {getattr(args, 'ppo_steps', 100000):,})...")
                async def run_ppo():
                    from data.collectors.rest_collector import RestCollector
                    from data.processors.candle_processor import CandleProcessor
                    collector = RestCollector()
                    processor = CandleProcessor()
                    dfs = []
                    for m in ["KRW-BTC", "KRW-ETH"]:
                        df = await collector.get_ohlcv(m, "minute60", 500)
                        if df is not None:
                            p = await processor.process(m, df, "60")
                            if p is not None:
                                dfs.append(p)
                    if not dfs:
                        print("데이터 수집 실패")
                        return
                    import pandas as pd
                    combined = pd.concat(dfs, ignore_index=True)
                    agent = PPOTradingAgent(use_gpu=True)
                    result = agent.train(combined, total_timesteps=getattr(args, 'ppo_steps', 100000))
                    print(f"\n=== PPO 훈련 결과 ===")
                    print(f"  PnL    : {result.get('pnl_pct', 0):+.2f}%")
                    print(f"  승률   : {result.get('win_rate', 0):.1f}%")
                    print(f"  샤프   : {result.get('sharpe', 0):.3f}")
                    print(f"\n✅ 모델 저장 완료: models/saved/ppo/")
                asyncio.run(run_ppo())
        
        elif args.mode == "news-check":
            # 뉴스 감성 분석 상태 확인
            import config.settings as settings_module
            settings_module._settings = Settings(mode="paper")
            from signals.filters.news_sentiment import NewsSentimentAnalyzer
            
            async def run_news():
                analyzer = NewsSentimentAnalyzer()
                n = await analyzer.fetch_news()
                print(f"\n📰 수집된 뉴스: {n}건")
                summary = analyzer.get_dashboard_summary()
                print(f"\n=== 뉴스 감성 요약 ===")
                print(f"  전체 뉴스: {summary['total_news']}건")
                print(f"  긍정: {summary['positive']} | 부정: {summary['negative']} | 중립: {summary['neutral']}")
                print(f"  전체 감성 점수: {summary['global_sentiment']:+.3f}")
                print("\n=== 마켓별 감성 ===")
                for m in ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP", "KRW-ADA"]:
                    score, boost = analyzer.get_signal_boost(m)
                    can_buy, reason = analyzer.can_buy(m)
                    print(
                        f"  {m}: 감성={score:+.3f} | 신호보정={boost:+.2f} | "
                        f"{'✅ 매수가능' if can_buy else '❌ 차단'}"
                    )
                print("\n=== 최근 뉴스 ===")
                for news in analyzer.get_recent_news(n=5):
                    sentiment_icon = "📈" if news['sentiment'] > 0.2 else ("📉" if news['sentiment'] < -0.2 else "➡️")
                    print(f"  [{news['time']}] {sentiment_icon} {news['title'][:60]}...")
            asyncio.run(run_news())
    except KeyboardInterrupt:
        print("\n\n👋 APEX BOT 종료됨")
    except Exception as e:
        logger.error(f"실행 오류: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
