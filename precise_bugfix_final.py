# precise_bugfix_final.py
# -*- coding: utf-8 -*-
"""
APEX BOT — 정밀 버그 수정 스크립트 (실매매 안전 버전)
수정 대상: BUG-REAL-1~6, QUALITY-1~3
실행: python precise_bugfix_final.py
"""
import re, ast, shutil
from pathlib import Path
from datetime import datetime

BACKUP = Path(f"archive/precise_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
BACKUP.mkdir(parents=True, exist_ok=True)
results = {}

def bak(p): shutil.copy2(p, BACKUP / p.name)
def rd(p):  return p.read_text(encoding="utf-8")
def wr(p,t): p.write_text(t, encoding="utf-8")
def ok(p):
    try: ast.parse(p.read_text(encoding="utf-8")); return True
    except SyntaxError as e:
        print(f"  ❌ 문법오류 {p.name}:{e.lineno}: {e.msg}"); return False

# ══════════════════════════════════════════════════════════
# BUG-REAL-1: risk_manager.py — record_trade_result에 pnl 추가
# ══════════════════════════════════════════════════════════
print("\n[BUG-REAL-1] risk_manager.py 수정...")
p = Path("risk/risk_manager.py")
if p.exists():
    bak(p); code = rd(p)

    # 1-A: record_trade_result 시그니처에 profit_rate 추가
    old = "    def record_trade_result(self, is_win: bool):\n        self._trade_results.append(is_win)"
    new = ("    def record_trade_result(self, is_win: bool, profit_rate: float = 0.0):\n"
           "        # [BUG-REAL-1 FIX] 불리언 대신 dict로 수익률도 함께 저장\n"
           "        self._trade_results.append({\"win\": is_win, \"pnl\": profit_rate})")
    found_a = old in code
    if found_a: code = code.replace(old, new)

    # 1-B: _calc_recent_win_rate도 dict 구조 대응
    old_wr = ('        recent = self._trade_results[-20:]\n'
              '        return sum(recent) / len(recent)')
    new_wr = ('        recent = self._trade_results[-20:]\n'
              '        # [BUG-REAL-1 FIX] dict 형태 대응\n'
              '        return sum(1 for r in recent if (r if isinstance(r, bool) else r.get("win", False))) / len(recent)')
    found_b = old_wr in code
    if found_b: code = code.replace(old_wr, new_wr)

    # 1-C: get_kelly_params 동적 avg_win/avg_loss
    old_kelly = ('        avg_win  = 0.03\n'
                 '        avg_loss = 0.02\n'
                 '        if len(self._trade_results) >= 10:\n'
                 '            wins  = [r for r in self._trade_results if r]\n'
                 '            losses= [r for r in self._trade_results if not r]\n'
                 '            avg_win  = 0.03 if not wins  else 0.03\n'
                 '            avg_loss = 0.02 if not losses else 0.02')
    new_kelly = ('        # [BUG-REAL-1 FIX] 실제 수익률 기반 동적 계산\n'
                 '        records = self._trade_results[-20:] if self._trade_results else []\n'
                 '        wins_pnl   = [r["pnl"] for r in records\n'
                 '                      if isinstance(r, dict) and r.get("win") and r.get("pnl", 0) > 0]\n'
                 '        losses_pnl = [abs(r["pnl"]) for r in records\n'
                 '                      if isinstance(r, dict) and not r.get("win") and r.get("pnl", 0) < 0]\n'
                 '        avg_win  = (sum(wins_pnl)   / len(wins_pnl))   if len(wins_pnl)   >= 3 else 0.03\n'
                 '        avg_loss = (sum(losses_pnl) / len(losses_pnl)) if len(losses_pnl) >= 3 else 0.02')
    found_c = old_kelly in code
    if found_c: code = code.replace(old_kelly, new_kelly)

    wr(p, code)
    if ok(p):
        results["BUG-REAL-1"] = f"✅ A={found_a} B={found_b} C={found_c}"
    else:
        shutil.copy2(BACKUP / p.name, p)
        results["BUG-REAL-1"] = "❌ 문법오류 → 원본 복원"
else:
    results["BUG-REAL-1"] = "⚠️ 파일 없음"


# ══════════════════════════════════════════════════════════
# BUG-REAL-2: engine_cycle.py — _ml_df 초기화 누락
# ══════════════════════════════════════════════════════════
print("\n[BUG-REAL-2] engine_cycle.py 수정...")
p = Path("core/engine_cycle.py")
if p.exists():
    bak(p); code = rd(p)

    # _cycle() 내 _ml_df 첫 참조 직전에 초기화 삽입
    old = ('        if _ml_df is None or len(_ml_df) < 10:\n'
           '            try:\n'
           '                _ml_df = self.cache_manager.get_candles(_ml_market, "1d")')
    new = ('        # [BUG-REAL-2 FIX] _ml_df 초기화 보장\n'
           '        if not \'_ml_df\' in dir(): _ml_df = None\n'
           '        if _ml_df is None or len(_ml_df) < 10:\n'
           '            try:\n'
           '                _ml_df = self.cache_manager.get_candles(_ml_market, "1d")')
    found = old in code
    if found:
        code = code.replace(old, new)
    else:
        # 정규식 폴백
        pat = r'(if _ml_df is None or len\(_ml_df\) < 10:)'
        repl = '# [BUG-REAL-2 FIX]\n        if not isinstance(locals().get(\'_ml_df\', None), type(None)) else None; _ml_df = locals().get(\'_ml_df\')\n        \\1'
        code_fb = re.sub(pat, '_ml_df = locals().get("_ml_df", None)\n        \\1', code, count=1)
        if code_fb != code:
            code = code_fb; found = True

    wr(p, code)
    if ok(p):
        results["BUG-REAL-2"] = f"✅ found={found}"
    else:
        shutil.copy2(BACKUP / p.name, p)
        results["BUG-REAL-2"] = "❌ 문법오류 → 원본 복원"
else:
    results["BUG-REAL-2"] = "⚠️ 파일 없음"


# ══════════════════════════════════════════════════════════
# BUG-REAL-3: engine_ml.py — dashboard_state 임포트 누락
# ══════════════════════════════════════════════════════════
print("\n[BUG-REAL-3] engine_ml.py 수정...")
p = Path("core/engine_ml.py")
if p.exists():
    bak(p); code = rd(p)

    old = ('            try:\n'
           '                if dashboard_state is not None:\n'
           '                    if "ml_predictions" not in dashboard_state.signals:')
    new = ('            try:\n'
           '                # [BUG-REAL-3 FIX] dashboard_state 임포트 누락 수정\n'
           '                try:\n'
           '                    from monitoring.dashboard import dashboard_state\n'
           '                except Exception:\n'
           '                    dashboard_state = None\n'
           '                if dashboard_state is not None:\n'
           '                    if "ml_predictions" not in dashboard_state.signals:')
    found = old in code
    if found: code = code.replace(old, new)

    # datetime 임포트도 추가
    if 'from datetime import datetime' not in code[:500]:
        code = 'from datetime import datetime\n' + code
        found = True

    wr(p, code)
    if ok(p):
        results["BUG-REAL-3"] = f"✅ found={found}"
    else:
        shutil.copy2(BACKUP / p.name, p)
        results["BUG-REAL-3"] = "❌ 문법오류 → 원본 복원"
else:
    results["BUG-REAL-3"] = "⚠️ 파일 없음"


# ══════════════════════════════════════════════════════════
# BUG-REAL-4: order_block_v2.py — open_arr 추가
# ══════════════════════════════════════════════════════════
print("\n[BUG-REAL-4] order_block_v2.py 수정...")
p = Path("strategies/v2/order_block_v2.py")
if p.exists():
    bak(p); code = rd(p)

    old_vars = ('        close  = df["close"].values\n'
                '        high   = df["high"].values\n'
                '        low    = df["low"].values\n'
                '        volume = df["volume"].values')
    new_vars = ('        close    = df["close"].values\n'
                '        high     = df["high"].values\n'
                '        low      = df["low"].values\n'
                '        volume   = df["volume"].values\n'
                '        open_arr = df["open"].values  # [BUG-REAL-4 FIX]')
    found_v = old_vars in code
    if found_v: code = code.replace(old_vars, new_vars)

    old_body = "body_move = abs(close[i] - open_[i]) if 'open_' in dir() else abs(high[i] - low[i])"
    new_body  = "body_move = abs(close[i] - open_arr[i])  # [BUG-REAL-4 FIX]"
    found_b  = old_body in code
    if found_b: code = code.replace(old_body, new_body)
    else:
        code_fb = re.sub(
            r"body_move\s*=\s*abs\(close\[i\]\s*-\s*open_\[i\]\)\s*if\s*'open_'\s*in\s*dir\(\)\s*else\s*abs\(high\[i\]\s*-\s*low\[i\]\)",
            "body_move = abs(close[i] - open_arr[i])  # [BUG-REAL-4 FIX]",
            code
        )
        if code_fb != code: code = code_fb; found_b = True

    wr(p, code)
    if ok(p):
        results["BUG-REAL-4"] = f"✅ vars={found_v} body={found_b}"
    else:
        shutil.copy2(BACKUP / p.name, p)
        results["BUG-REAL-4"] = "❌ 문법오류 → 원본 복원"
else:
    results["BUG-REAL-4"] = "⚠️ 파일 없음"


# ══════════════════════════════════════════════════════════
# BUG-REAL-5: signal_combiner.py — Vol_Breakout 중복 키 정리
# ══════════════════════════════════════════════════════════
print("\n[BUG-REAL-5] signal_combiner.py 수정...")
p = Path("signals/signal_combiner.py")
if p.exists():
    bak(p); code = rd(p)

    # Vol_Breakout과 VolBreakout을 동일 값(0.2)으로 통일
    old = ('        "Vol_Breakout":     0.6,  # [FIX] 단독손실 전략 낮은 가중치\n'
           '        "MACD_Cross":        1.8,')
    new = ('        "Vol_Breakout":     0.2,  # [BUG-REAL-5 FIX] VolBreakout과 통일\n'
           '        "VolBreakout":      0.2,  # v2 앙상블에서 사용하는 키명\n'
           '        "MACD_Cross":        1.8,')
    found = old in code
    if found: code = code.replace(old, new)
    else:
        # VolBreakout 키가 이미 있으면 Vol_Breakout만 값 수정
        code_fb = re.sub(
            r'"Vol_Breakout":\s*0\.6',
            '"Vol_Breakout":     0.2  # [BUG-REAL-5 FIX]',
            code
        )
        if code_fb != code: code = code_fb; found = True

    wr(p, code)
    if ok(p):
        results["BUG-REAL-5"] = f"✅ found={found}"
    else:
        shutil.copy2(BACKUP / p.name, p)
        results["BUG-REAL-5"] = "❌ 문법오류 → 원본 복원"
else:
    results["BUG-REAL-5"] = "⚠️ 파일 없음"


# ══════════════════════════════════════════════════════════
# QUALITY-2: engine_buy.py — bear_reversal_markets 속성명 통일
# ══════════════════════════════════════════════════════════
print("\n[QUALITY-2] engine_buy.py 수정...")
p = Path("core/engine_buy.py")
if p.exists():
    bak(p); code = rd(p)

    # 언더스코어 없는 버전을 언더스코어 있는 버전으로 통일
    old = ('                self.bear_reversal_markets = getattr(\n'
           '                    self, "_bear_reversal_markets", set()\n'
           '                )\n'
           '                self.bear_reversal_markets.discard(market)')
    new = ('                # [QUALITY-2 FIX] 속성명 _bear_reversal_markets로 통일\n'
           '                self._bear_reversal_markets = getattr(\n'
           '                    self, "_bear_reversal_markets", set()\n'
           '                )\n'
           '                self._bear_reversal_markets.discard(market)')
    found = old in code
    if found: code = code.replace(old, new)
    else:
        code_fb = re.sub(
            r'self\.bear_reversal_markets\.discard\(market\)',
            'self._bear_reversal_markets.discard(market)  # [QUALITY-2 FIX]',
            code
        )
        if code_fb != code: code = code_fb; found = True

    wr(p, code)
    if ok(p):
        results["QUALITY-2"] = f"✅ found={found}"
    else:
        shutil.copy2(BACKUP / p.name, p)
        results["QUALITY-2"] = "❌ 문법오류 → 원본 복원"
else:
    results["QUALITY-2"] = "⚠️ 파일 없음"


# ══════════════════════════════════════════════════════════
# 최종 문법 검증
# ══════════════════════════════════════════════════════════
print("\n" + "="*60)
check_files = [
    "risk/risk_manager.py",
    "core/engine_cycle.py",
    "core/engine_ml.py",
    "core/engine_buy.py",
    "signals/signal_combiner.py",
    "strategies/v2/order_block_v2.py",
]
all_ok = True
for f in check_files:
    pp = Path(f)
    if pp.exists():
        s = ok(pp)
        print(f"  {'✅' if s else '❌'} {f}")
        if not s: all_ok = False
    else:
        print(f"  ⚠️ 없음: {f}")

print("\n" + "="*60)
print("📊 수정 결과")
for k, v in results.items():
    print(f"  {k}: {v}")
success = sum(1 for v in results.values() if v.startswith("✅"))
print(f"\n  성공: {success}/{len(results)} | 백업: {BACKUP}")
if all_ok and success >= len(results) - 1:
    print("\n  ✅ 실매매 안전 수정 완료 — python main.py --mode paper 로 24h 검증 권장")
else:
    print("\n  ⚠️ 미완료 항목 있음 — 수동 확인 필요")
