"""
핵심 파일만 압축 스캔 - 주석/빈줄 제거로 용량 최소화
목표: 전체 내용을 한 번에 Claude에게 붙여넣기 가능한 크기로
"""
from pathlib import Path
import re

OUTPUT = "core_scan.txt"

# 분석할 핵심 파일 목록 (우선순위 순)
CORE_FILES = [
    "risk/stop_loss/atr_stop.py",
    "risk/stop_loss/trailing_stop.py",
    "risk/position_sizer.py",
    "risk/partial_exit.py",
    "risk/risk_manager.py",
    "risk/position_manager_v2.py",
    "signals/signal_combiner.py",
    "signals/filters/regime_detector.py",
    "signals/filters/trend_filter.py",
    "signals/filters/volume_profile.py",
    "signals/filters/correlation_filter.py",
    "signals/filters/fear_greed.py",
    "signals/filters/volume_spike.py",
    "signals/filters/news_sentiment.py",
    "signals/filters/orderbook_signal.py",
    "signals/mtf_signal_merger.py",
    "data/collectors/rest_collector.py",
    "data/collectors/ws_collector.py",
    "data/processors/candle_processor.py",
    "data/storage/db_manager.py",
    "data/storage/cache_manager.py",
    "execution/upbit_adapter.py",
    "execution/executor.py",
    "execution/live_guard.py",
    "core/smart_wallet.py",
    "core/rate_limit_manager.py",
    "core/slippage_model.py",
    "core/portfolio_manager.py",
    "core/event_bus.py",
    "core/state_machine.py",
    "config/settings.py",
    "monitoring/telegram_bot.py",
    "monitoring/performance_tracker.py",
    "monitoring/paper_report.py",
    "monitoring/analytics/strategy_analyzer.py",
    "monitoring/analytics/live_readiness.py",
    "models/inference/predictor.py",
    "models/rl/ppo_agent.py",
    "models/train/auto_trainer.py",
    "strategies/base_strategy.py",
    "strategies/order_block_detector.py",
    "start_paper.py",
    "start_live.py",
]

def strip_code(src: str) -> str:
    """주석·docstring·빈줄 제거로 용량 압축"""
    # 한줄 주석 제거
    src = re.sub(r'#[^\n]*', '', src)
    # 빈줄 압축
    src = re.sub(r'\n\s*\n+', '\n', src)
    # 줄 앞뒤 공백 정리 (들여쓰기는 유지)
    lines = []
    for line in src.splitlines():
        stripped = line.rstrip()
        if stripped:
            lines.append(stripped)
    return '\n'.join(lines)

out_lines = []
total_orig = 0
total_comp = 0

out_lines.append("=" * 60)
out_lines.append("APEX BOT 핵심 파일 압축 스캔")
out_lines.append("=" * 60)

missing = []
for rel in CORE_FILES:
    p = Path(rel)
    if not p.exists():
        missing.append(rel)
        continue

    orig = p.read_text(encoding="utf-8", errors="ignore")
    comp = strip_code(orig)
    orig_kb = len(orig.encode()) / 1024
    comp_kb = len(comp.encode()) / 1024
    total_orig += orig_kb
    total_comp += comp_kb

    out_lines.append(f"\n{'='*60}")
    out_lines.append(f"FILE: {rel}  ({orig_kb:.1f}KB → {comp_kb:.1f}KB)")
    out_lines.append('=' * 60)
    for i, line in enumerate(comp.splitlines(), 1):
        out_lines.append(f"L{i:04d}: {line}")

if missing:
    out_lines.append("\n" + "=" * 60)
    out_lines.append("누락 파일 목록:")
    for m in missing:
        out_lines.append(f"  없음: {m}")

out_lines.append("\n" + "=" * 60)
out_lines.append(f"압축 전: {total_orig:.1f}KB  →  압축 후: {total_comp:.1f}KB")
out_lines.append(f"압축률: {(1 - total_comp/max(total_orig,1))*100:.0f}%")
out_lines.append("=" * 60)

result = '\n'.join(out_lines)
Path(OUTPUT).write_text(result, encoding="utf-8")

print(f"✅ 스캔 완료 → {OUTPUT}")
print(f"   원본: {total_orig:.1f}KB → 압축: {total_comp:.1f}KB")
print(f"   압축률: {(1 - total_comp/max(total_orig,1))*100:.0f}%")
print(f"   총 줄 수: {len(out_lines):,}")

if total_comp < 200:
    print("✅ 붙여넣기 가능한 크기입니다!")
else:
    print(f"⚠️  {total_comp:.0f}KB - 분할 필요할 수 있음")
    print("   아래 명령으로 분할해서 붙여넣으세요:")
    print('   Get-Content "core_scan.txt" -Encoding UTF8 | Select-Object -First 1000')
