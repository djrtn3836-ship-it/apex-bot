"""
APEX BOT - ML 실시간 예측 모니터
봇이 실행 중일 때 ML 추론 결과를 실시간으로 확인하는 CLI 도구

사용법:
  python tools/ml_monitor.py           # 전체 코인 ML 예측 1회 출력
  python tools/ml_monitor.py --watch   # 60초마다 자동 갱신
  python tools/ml_monitor.py --coin BTC  # 특정 코인만
"""

import sys
import os
import time
import argparse
import asyncio
from pathlib import Path
from datetime import datetime

# 프로젝트 루트를 경로에 추가
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── 의존성 체크 ───────────────────────────────────────────────────
try:
    import torch
    TORCH_OK = True
except ImportError:
    TORCH_OK = False

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.live import Live
    from rich.text import Text
    from rich.columns import Columns
    from rich import box
    RICH_OK = True
except ImportError:
    RICH_OK = False

# ─────────────────────────────────────────────────────────────────
console = Console() if RICH_OK else None

COINS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA",
         "KRW-DOGE", "KRW-AVAX", "KRW-DOT", "KRW-LINK", "KRW-ATOM"]

SIGNAL_STYLE = {
    "BUY":  ("🟢", "bold green"),
    "SELL": ("🔴", "bold red"),
    "HOLD": ("⚪", "dim white"),
}


async def fetch_ohlcv(market: str, count: int = 80):
    """Upbit REST API로 60분봉 데이터 수집"""
    try:
        from data.collectors.rest_collector import RestDataCollector
        collector = RestDataCollector()
        df = await collector.get_ohlcv(market, "minute60", count)
        return df
    except Exception as e:
        return None


async def run_ml_predict(market: str, df):
    """ML 앙상블 예측 실행"""
    if df is None or len(df) < 60:
        return None
    try:
        from data.processors.candle_processor import CandleProcessor
        from models.inference.predictor import MLPredictor

        processor = CandleProcessor()
        df_proc = processor.process(df)
        if df_proc is None or len(df_proc) < 60:
            return None

        predictor = MLPredictor()
        ok = predictor.load_model()

        if not ok:
            # 저장된 모델 없음 → 신규 초기화 모델로 예측 (참고용)
            pass

        result = predictor.predict(market, df_proc)
        return result
    except Exception as e:
        return {"error": str(e)}


def make_signal_bar(buy: float, hold: float, sell: float, width: int = 20) -> str:
    """확률 막대 시각화"""
    b = int(buy * width)
    h = int(hold * width)
    s = width - b - h
    return f"[green]{'█' * b}[/green][white]{'░' * h}[/white][red]{'█' * max(0,s)}[/red]"


async def predict_all(target_coins: list) -> list:
    """모든 코인 예측 수행"""
    results = []
    for market in target_coins:
        coin = market.replace("KRW-", "")
        df = await fetch_ohlcv(market, 80)
        if df is None:
            results.append({
                "market": market, "coin": coin,
                "signal": "ERROR", "confidence": 0,
                "buy_prob": 0, "hold_prob": 0, "sell_prob": 0,
                "model_agreement": 0, "inference_ms": 0,
                "data_rows": 0
            })
            continue

        pred = await run_ml_predict(market, df)
        if pred is None:
            results.append({
                "market": market, "coin": coin,
                "signal": "NO_DATA", "confidence": 0,
                "buy_prob": 0, "hold_prob": 0, "sell_prob": 0,
                "model_agreement": 0, "inference_ms": 0,
                "data_rows": len(df)
            })
        elif "error" in pred:
            results.append({
                "market": market, "coin": coin,
                "signal": "ERROR", "confidence": 0,
                "buy_prob": 0, "hold_prob": 0, "sell_prob": 0,
                "model_agreement": 0, "inference_ms": 0,
                "data_rows": len(df),
                "error": pred["error"]
            })
        else:
            pred["coin"] = coin
            pred["data_rows"] = len(df)
            results.append(pred)

    return results


def print_results_rich(results: list, model_loaded: bool):
    """Rich 테이블로 결과 출력"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── 헤더 패널 ──
    model_status = "[green]✅ 저장된 모델 사용[/green]" if model_loaded else "[yellow]⚠️  신규 초기화 모델 (미훈련)[/yellow]"
    header = f"[bold cyan]APEX BOT ML 실시간 예측[/bold cyan]  {model_status}\n[dim]{now}[/dim]"
    console.print(Panel(header, box=box.ROUNDED))

    # ── 예측 테이블 ──
    table = Table(
        title="",
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold cyan",
        min_width=90,
    )
    table.add_column("코인",         style="bold", width=7)
    table.add_column("신호",         justify="center", width=8)
    table.add_column("신뢰도",       justify="center", width=8)
    table.add_column("BUY %",        justify="center", width=7)
    table.add_column("HOLD %",       justify="center", width=7)
    table.add_column("SELL %",       justify="center", width=7)
    table.add_column("확률 분포",    width=24)
    table.add_column("모델 동의율",  justify="center", width=10)
    table.add_column("추론(ms)",     justify="right",  width=9)

    buy_coins  = []
    sell_coins = []
    hold_coins = []

    for r in results:
        signal = r.get("signal", "?")
        emoji, style = SIGNAL_STYLE.get(signal, ("❓", "white"))
        conf     = r.get("confidence", 0)
        buy_p    = r.get("buy_prob",  0)
        hold_p   = r.get("hold_prob", 0)
        sell_p   = r.get("sell_prob", 0)
        agree    = r.get("model_agreement", 0)
        ms       = r.get("inference_ms", 0)
        err      = r.get("error", "")

        if err:
            table.add_row(
                r.get("coin","?"),
                "[red]ERROR[/red]",
                "-", "-", "-", "-",
                f"[red]{err[:25]}[/red]",
                "-", "-"
            )
            continue

        if signal == "NO_DATA":
            table.add_row(
                r.get("coin","?"),
                "[dim]NO DATA[/dim]",
                "-", "-", "-", "-",
                f"[dim]데이터 {r.get('data_rows',0)}행[/dim]",
                "-", "-"
            )
            continue

        # 신뢰도 색상
        conf_color = "green" if conf >= 0.7 else ("yellow" if conf >= 0.5 else "red")
        # 동의율 색상
        agree_color = "green" if agree >= 0.9 else ("yellow" if agree >= 0.6 else "red")

        bar = make_signal_bar(buy_p, hold_p, sell_p)

        table.add_row(
            f"[bold]{r.get('coin','?')}[/bold]",
            f"{emoji} [{style}]{signal}[/{style}]",
            f"[{conf_color}]{conf:.1%}[/{conf_color}]",
            f"[green]{buy_p:.1%}[/green]",
            f"[white]{hold_p:.1%}[/white]",
            f"[red]{sell_p:.1%}[/red]",
            bar,
            f"[{agree_color}]{agree:.0%}[/{agree_color}]",
            f"[dim]{ms:.1f}[/dim]" if ms > 0 else "[dim]-[/dim]",
        )

        if signal == "BUY":   buy_coins.append(r.get("coin","?"))
        elif signal == "SELL": sell_coins.append(r.get("coin","?"))
        else:                  hold_coins.append(r.get("coin","?"))

    console.print(table)

    # ── 요약 ──
    summary_parts = []
    if buy_coins:
        summary_parts.append(f"[bold green]🟢 BUY[/bold green]: {', '.join(buy_coins)}")
    if sell_coins:
        summary_parts.append(f"[bold red]🔴 SELL[/bold red]: {', '.join(sell_coins)}")
    if hold_coins:
        summary_parts.append(f"[dim]⚪ HOLD[/dim]: {', '.join(hold_coins)}")

    if summary_parts:
        console.print(Panel(
            "\n".join(summary_parts),
            title="[bold]📊 요약[/bold]",
            box=box.ROUNDED,
            padding=(0, 2),
        ))

    # ── 경고 ──
    if not model_loaded:
        console.print(Panel(
            "[yellow]⚠️  저장된 모델이 없습니다!\n"
            "봇을 실행하면 10분 후 PPO 자동 훈련, 24시간 후 ML 재학습이 시작됩니다.\n"
            "현재 예측값은 [bold]랜덤 초기화 모델[/bold]이므로 실제 매매 판단에 사용하지 마세요.[/yellow]",
            title="[bold yellow]주의[/bold yellow]",
            box=box.ROUNDED,
        ))


def print_results_plain(results: list):
    """Rich 없을 때 일반 텍스트 출력"""
    print(f"\n{'='*60}")
    print(f"  APEX BOT ML 예측 결과  {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}")
    print(f"{'코인':<8} {'신호':<6} {'신뢰도':<8} {'BUY':>6} {'HOLD':>6} {'SELL':>6} {'동의율':>7}")
    print("-"*60)
    for r in results:
        signal = r.get("signal","?")
        print(f"{r.get('coin','?'):<8} {signal:<6} "
              f"{r.get('confidence',0):>7.1%} "
              f"{r.get('buy_prob',0):>6.1%} "
              f"{r.get('hold_prob',0):>6.1%} "
              f"{r.get('sell_prob',0):>6.1%} "
              f"{r.get('model_agreement',0):>7.1%}")
    print("="*60)


async def main():
    parser = argparse.ArgumentParser(description="APEX BOT ML 실시간 예측 모니터")
    parser.add_argument("--watch", "-w", action="store_true",
                        help="60초마다 자동 갱신 (Ctrl+C로 종료)")
    parser.add_argument("--interval", "-i", type=int, default=60,
                        help="갱신 주기 (초, 기본값: 60)")
    parser.add_argument("--coin", "-c", type=str, default=None,
                        help="특정 코인만 확인 (예: BTC, ETH)")
    args = parser.parse_args()

    # 대상 코인 결정
    if args.coin:
        coin_name = args.coin.upper().replace("KRW-", "")
        target = [f"KRW-{coin_name}"]
    else:
        target = COINS

    # 모델 로드 여부 확인
    model_path = ROOT / "models" / "saved" / "ensemble_best.pt"
    model_loaded = model_path.exists()

    if RICH_OK:
        console.print(f"\n[cyan]🔍 ML 예측 수행 중... ({len(target)}개 코인)[/cyan]")
    else:
        print(f"\n ML 예측 수행 중... ({len(target)}개 코인)")

    if not args.watch:
        # 1회 실행
        results = await predict_all(target)
        if RICH_OK:
            print_results_rich(results, model_loaded)
        else:
            print_results_plain(results)
    else:
        # 반복 실행
        if RICH_OK:
            console.print(f"[dim]⏱  {args.interval}초마다 자동 갱신 | Ctrl+C 로 종료[/dim]\n")
        try:
            while True:
                results = await predict_all(target)
                if RICH_OK:
                    console.clear()
                    print_results_rich(results, model_loaded)
                    console.print(f"\n[dim]다음 갱신까지 {args.interval}초 대기... (Ctrl+C 종료)[/dim]")
                else:
                    print_results_plain(results)
                    print(f"  → {args.interval}초 후 갱신 (Ctrl+C 종료)")
                await asyncio.sleep(args.interval)
        except KeyboardInterrupt:
            if RICH_OK:
                console.print("\n[yellow]모니터 종료[/yellow]")
            else:
                print("\n종료")


if __name__ == "__main__":
    asyncio.run(main())
