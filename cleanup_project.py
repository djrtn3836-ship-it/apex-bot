# cleanup_project.py
import os
import shutil
from pathlib import Path

BASE = Path('.')
ARCHIVE = BASE / '_archive'
ARCHIVE.mkdir(exist_ok=True)

# ── STEP 1: fix_*.py / diag_*.py / 일회성 스크립트 아카이브 ────────────
step1_files = [
    'fix_orderbook_init.py', 'fix_orderbook_v2.py', 'fix_ob_logger.py',
    'fix_telegram_notify.py', 'fix_ws_orderbook.py', 'fix_main_loop.py',
    'fix_scheduler_name.py', 'fix_add_method.py', 'fix_unlimited_analysis.py',
    'fix_cache_and_ws.py', 'fix_final.py', 'fix_position_access.py',
    'fix_candle_final.py', 'fix_candle2.py', 'fix_ml_pred.py',
    'fix_circuit_breaker.py', 'fix_signal_combiner.py', 'fix_signal_combiner2.py',
    'fix_sc_final.py', 'fix_orderbook_class.py',
    'diag_db.py', 'diag_candle.py', 'integrate_orderbook.py',
    'verify_fixes.py', 'upgrade_all.py', 'reset_and_restart.py',
]

print(" STEP 1:    ")
for f in step1_files:
    src = BASE / f
    if src.exists():
        shutil.move(str(src), str(ARCHIVE / f))
        print(f"   {f}")
    else:
        print(f"   {f} ()")

# ── STEP 2: 구버전 루트 전략 파일 아카이브 ──────────────────────────────
step2_files = [
    'strategies/macd_momentum.py', 'strategies/mean_reversion.py',
    'strategies/ml_strategy.py',   'strategies/order_block_smc.py',
    'strategies/rsi_divergence.py','strategies/trend_following.py',
    'strategies/volatility_breakout.py', 'strategies/volume_spike.py',
]

(ARCHIVE / 'strategies_old').mkdir(exist_ok=True)
print("\n STEP 2:      ")
for f in step2_files:
    src = BASE / f
    if src.exists():
        dst = ARCHIVE / 'strategies_old' / Path(f).name
        shutil.move(str(src), str(dst))
        print(f"   {f}")
    else:
        print(f"   {f} ()")

# ── STEP 3: engine_patch.py 아카이브 ────────────────────────────────────
print("\n STEP 3: engine_patch.py  ")
ep = BASE / 'core' / 'engine_patch.py'
if ep.exists():
    shutil.move(str(ep), str(ARCHIVE / 'engine_patch.py'))
    print("   core/engine_patch.py")

# ── STEP 4: 중복 백테스팅 파일 확인 후 아카이브 ─────────────────────────
print("\n STEP 4:     ")
bt_dup = BASE / 'backtesting' / 'optimizer.py'
if bt_dup.exists():
    shutil.move(str(bt_dup), str(ARCHIVE / 'backtesting_optimizer_old.py'))
    print("   backtesting/optimizer.py ()")

# ── STEP 5: dashboard_v2 아카이브 ───────────────────────────────────────
print("\n STEP 5: monitoring/dashboard_v2  ")
dv2 = BASE / 'monitoring' / 'dashboard_v2'
if dv2.exists():
    shutil.move(str(dv2), str(ARCHIVE / 'dashboard_v2'))
    print("   monitoring/dashboard_v2/")

# ── STEP 6: news_sentiment_patch.py 아카이브 ────────────────────────────
print("\n STEP 6:    ")
nsp = BASE / 'signals' / 'filters' / 'news_sentiment_patch.py'
if nsp.exists():
    shutil.move(str(nsp), str(ARCHIVE / 'news_sentiment_patch.py'))
    print("   signals/filters/news_sentiment_patch.py")

print("\n    ")
for f in sorted(ARCHIVE.rglob('*.py')):
    print(f"  {f.relative_to(ARCHIVE)}")

print(f"\n  : {ARCHIVE}  ")
print("       _archive    ")
