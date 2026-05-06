from pathlib import Path
import py_compile, shutil
from datetime import datetime

ARCHIVE = Path(f"archive/fx7_{datetime.now():%Y%m%d_%H%M%S}")
ARCHIVE.mkdir(parents=True, exist_ok=True)

# ── FX7-1: ensemble_engine.py OrderBlock_SMC _strategies에서 제거 ──
p1 = Path("strategies/v2/ensemble_engine.py")
shutil.copy2(p1, ARCHIVE / "ensemble_engine.py")
t1 = p1.read_text(encoding="utf-8")
old1 = '            "OrderBlock_SMC":    OrderBlockStrategy2(),'
new1 = '            # [FX7-1] OrderBlock_SMC 완전 비활성화 (weight=0.0, 6건 전패)\n            # "OrderBlock_SMC":    OrderBlockStrategy2(),'
if old1 in t1 and "[FX7-1]" not in t1:
    p1.write_text(t1.replace(old1, new1), encoding="utf-8")
    print("OK   FX7-1 OrderBlock_SMC _strategies 제거")
else:
    print("SKIP FX7-1:", "이미적용" if "[FX7-1]" in t1 else "패턴없음")

# ── FX7-2: db_manager.py 테이블에 win_rate, open_positions 컬럼 추가 ──
p2 = Path("data/storage/db_manager.py")
shutil.copy2(p2, ARCHIVE / "db_manager.py")
t2 = p2.read_text(encoding="utf-8")
old2 = "                sharpe_ratio REAL\n            )"
new2 = "                sharpe_ratio REAL,\n                win_rate REAL DEFAULT 0,\n                open_positions INTEGER DEFAULT 0\n            )  -- [FX7-2]"
if old2 in t2 and "[FX7-2]" not in t2:
    p2.write_text(t2.replace(old2, new2), encoding="utf-8")
    print("OK   FX7-2 daily_performance 컬럼 추가")
else:
    print("SKIP FX7-2:", "이미적용" if "[FX7-2]" in t2 else "패턴없음")

# ── FX7-3: save_daily_performance INSERT에 win_rate, open_positions 추가 ──
t2b = p2.read_text(encoding="utf-8")
old3 = """                    INSERT OR REPLACE INTO daily_performance
                    (date, total_assets, daily_pnl, trade_count, win_count,
                     max_drawdown, sharpe_ratio)
                    VALUES (?, ?, ?, ?, ?, ?, ?)"""
new3 = """                    INSERT OR REPLACE INTO daily_performance
                    (date, total_assets, daily_pnl, trade_count, win_count,
                     max_drawdown, sharpe_ratio, win_rate, open_positions)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)  -- [FX7-3]"""
if old3 in t2b and "[FX7-3]" not in t2b:
    # VALUES 인수도 확장
    old3b = """                        perf.get("max_drawdown", 0),
                        perf.get("sharpe_ratio", 0),
                    )"""
    new3b = """                        perf.get("max_drawdown", 0),
                        perf.get("sharpe_ratio", 0),
                        perf.get("win_rate", 0),
                        perf.get("open_positions", 0),
                    )"""
    t2c = t2b.replace(old3, new3).replace(old3b, new3b)
    p2.write_text(t2c, encoding="utf-8")
    print("OK   FX7-3 save_daily_performance INSERT 확장")
else:
    print("SKIP FX7-3:", "이미적용" if "[FX7-3]" in t2b else "패턴없음")

# ── 컴파일 검증 ──
for fp in [p1, p2]:
    try:
        py_compile.compile(str(fp), doraise=True)
        print(f"컴파일 OK  {fp}")
    except Exception as e:
        print(f"컴파일 FAIL {fp}: {e}")

print("백업:", ARCHIVE)
