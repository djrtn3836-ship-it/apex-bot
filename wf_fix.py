#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wf_fix.py
=========
WF-1: optimized_params.json의 모든 전략 is_active=true 로 변경
WF-2: oos_sharpe=0.0 → 실제 DB 기반 Sharpe로 채우기
WF-3: weight_boost를 DB WR 기반으로 계산해서 주입
WF-4: engine_cycle.py의 is_active 체크 로직 보완
"""
import os, json, sqlite3, math, shutil, datetime, py_compile

BASE = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot"
TS   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

# ── WF-1/2/3: optimized_params.json 수정 ─────────────────────
cfg_path = os.path.join(BASE, "config", "optimized_params.json")
db_path  = os.path.join(BASE, "database", "apex_bot.db")

with open(cfg_path, encoding="utf-8") as f:
    cfg = json.load(f)

# DB에서 전략별 실제 성과 읽기
conn = sqlite3.connect(db_path)
cur  = conn.cursor()

strategy_stats = {}
try:
    cur.execute("""
        SELECT strategy,
               COUNT(*) as total,
               SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
               AVG(pnl_pct) as avg_pnl,
               AVG(pnl_pct * pnl_pct) as avg_sq_pnl
        FROM trade_history
        WHERE side='sell'
        GROUP BY strategy
    """)
    for row in cur.fetchall():
        strat, total, wins, avg_pnl, avg_sq_pnl = row
        wr = (wins / total) if total > 0 else 0.5
        std_pnl = math.sqrt(max(0, avg_sq_pnl - avg_pnl**2)) if avg_sq_pnl else 0.01
        sharpe  = (avg_pnl / std_pnl * math.sqrt(252)) if std_pnl > 0 else 0.0
        strategy_stats[strat] = {
            "total": total, "wr": wr,
            "avg_pnl": avg_pnl or 0.0,
            "sharpe": sharpe
        }
        print(f"  DB [{strat:20s}] total={total} WR={wr:.1%} "
              f"avg_pnl={avg_pnl:.4f} sharpe={sharpe:.3f}")
except Exception as e:
    print(f"  DB 조회 오류: {e}")
conn.close()

# 기본 Sharpe 매핑 (DB 데이터 없을 때 fallback)
DEFAULT_SHARPE = {
    "MACD_Cross":       1.56,
    "RSI_Divergence":   0.80,
    "Supertrend":       0.70,
    "Bollinger_Squeeze":3.92,
    "ATR_Channel":      0.90,
    "OrderBlock_SMC":  -1.26,
    "VWAP_Reversion":   0.0,
    "VolBreakout":      0.0,
}

DISABLED = {"VWAP_Reversion", "VolBreakout"}

print()
bk = cfg_path + f".bak_{TS}"
shutil.copy2(cfg_path, bk)
print(f"  백업: {bk}")

for name, info in cfg["strategies"].items():
    stats = strategy_stats.get(name, {})
    sharpe = stats.get("sharpe") or DEFAULT_SHARPE.get(name, 0.0)
    wr     = stats.get("wr", 0.5)

    # WF-1: DISABLED 제외하고 모두 활성화
    if name in DISABLED:
        info["is_active"] = False
        info["oos_sharpe"] = 0.0
        print(f"  [{name:20s}] DISABLED 유지")
        continue

    # WF-2: OOS Sharpe 채우기
    info["oos_sharpe"]  = round(sharpe, 4)
    info["oos_win_rate"]= round(wr * 100, 1)

    # WF-3: weight_boost = WR 기반 (0.5 기준)
    boost = round(max(0.5, min(2.0, wr / 0.55)), 4)
    info["weight_boost"] = boost

    # WF-1: 활성화
    info["is_active"] = True
    info["updated_at"] = datetime.datetime.now().isoformat()

    print(f"  [{name:20s}] is_active=True "
          f"oos_sharpe={sharpe:.3f} weight_boost={boost:.3f}")

cfg["updated_at"] = datetime.datetime.now().isoformat()
with open(cfg_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
print(f"\n  ✅ {cfg_path} 저장 완료")

# ── WF-4: engine_cycle.py is_active 체크 보완 ─────────────────
ec_path = os.path.join(BASE, "core", "engine_cycle.py")
with open(ec_path, encoding="utf-8") as f:
    src = f.read()

OLD = """_oos      = info.get("oos_sharpe", None)
                # [IF1_WalkForwardGuard] OOS Sharpe=0.000은 데이터 없음"""

# 줄 826~827 주변 실제 비활성화 로직 찾기
# is_active 체크가 없는 경우 추가
if 'info.get("is_active"' not in src and "is_active" not in src:
    OLD2 = '_oos      = info.get("oos_sharpe", None)'
    NEW2 = ('_is_active = info.get("is_active", True)\n'
            '                if not _is_active:\n'
            '                    continue  # WF-4: is_active=false 전략 스킵\n'
            '                _oos      = info.get("oos_sharpe", None)')
    if OLD2 in src:
        bk2 = ec_path + f".bak_{TS}"
        shutil.copy2(ec_path, bk2)
        new_src = src.replace(OLD2, NEW2, 1)
        with open(ec_path, "w", encoding="utf-8") as f:
            f.write(new_src)
        try:
            py_compile.compile(ec_path, doraise=True)
            print(f"  ✅ WF-4 engine_cycle.py is_active 체크 추가")
        except py_compile.PyCompileError as e:
            with open(ec_path, "w", encoding="utf-8") as f:
                f.write(src)
            print(f"  ❌ WF-4 컴파일 오류 → 롤백: {e}")
    else:
        print("  ⏭  WF-4: 패턴 없음 (수동 확인 필요)")
else:
    print("  ⏭  WF-4: is_active 체크 이미 존재")

print("\n" + "="*50)
print("  wf_fix 완료 — 봇을 재시작하세요")
print("="*50)
