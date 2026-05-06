#!/usr/bin/env python3
# fix18.py — FX18-1~3 HIVE MTF soft-fail RSI 확장 / Surge MTF 바이패스

import os, re, shutil, py_compile, datetime

REPO    = os.path.dirname(os.path.abspath(__file__))
MTF_F   = os.path.join(REPO, "signals", "mtf_signal_merger.py")
BUY_F   = os.path.join(REPO, "core", "engine_buy.py")

ts  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
bak = os.path.join(REPO, "archive", f"fx18_{ts}")
os.makedirs(bak, exist_ok=True)
shutil.copy2(MTF_F, bak)
shutil.copy2(BUY_F, bak)
print(f"[BACKUP] {bak}")

results = []

# ── FX18-1: MTFMerger BULL soft-fail RSI 조건 확장 ──────────────────────
with open(MTF_F, encoding="utf-8") as f:
    src = f.read()

OLD18_1 = "_rsi_oversold   = avg_rsi <= 25"
NEW18_1 = (
    "_rsi_oversold   = (avg_rsi <= 40) or (score >= -0.35)"
    "  # [FX18-1] RSI 25→40 확장 + score 근접 soft-fail"
)

if OLD18_1 in src:
    src = src.replace(OLD18_1, NEW18_1, 1)
    with open(MTF_F, "w", encoding="utf-8") as f:
        f.write(src)
    results.append(("FX18-1", "✅", "MTFMerger RSI 조건 avg_rsi≤40 OR score≥-0.35 확장"))
else:
    results.append(("FX18-1", "⚠️", f"패턴 미발견 — 수동 확인 필요"))

# FX18-1 컴파일 검증
try:
    py_compile.compile(MTF_F, doraise=True)
    results.append(("FX18-1-compile", "✅", "mtf_signal_merger.py 컴파일 성공"))
except py_compile.PyCompileError as e:
    results.append(("FX18-1-compile", "❌", str(e)))

# ── FX18-3: engine_buy.py — SCR ≥15% Surge 시 MTF 차단 우회 ─────────────
with open(BUY_F, encoding="utf-8") as f:
    src = f.read()

# MTF DOWN 차단 직전에 Surge 우회 조건 삽입
OLD18_3 = (
    "if _mtf_dir <= -1 and not _is_bear_rev:\n"
    "                                logger.info(\n"
    "                                    f\" MTF  ({market}): \"\n"
    "                                    f\"방향={_mtf_result.final_direction.name} | \"\n"
    "                                    f\"{_mtf_result.reason}\"\n"
    "                                )\n"
    "                                return"
)
NEW18_3 = (
    "if _mtf_dir <= -1 and not _is_bear_rev:\n"
    "                                # [FX18-3] Surge ≥15% 시 MTF DOWN 차단 우회\n"
    "                                _fx18_scr = 0.0\n"
    "                                _scr_c = getattr(self, '_scr_cache', {})\n"
    "                                _fx18_scr = float(_scr_c.get(market, {}).get('scr', 0.0))\n"
    "                                if _fx18_scr == 0.0:\n"
    "                                    _mcr = getattr(self, '_market_change_rates', {})\n"
    "                                    _fx18_scr = float(_mcr.get(market, 0.0)) * 100\n"
    "                                _fx18_gr = str(getattr(\n"
    "                                    getattr(self, '_global_regime', None), 'value',\n"
    "                                    getattr(self, '_global_regime', 'UNKNOWN') or 'UNKNOWN'\n"
    "                                )).upper()\n"
    "                                _fx18_bull = _fx18_gr in ('BULL', 'TRENDING_UP', 'RECOVERY')\n"
    "                                if _fx18_bull and _fx18_scr >= 15.0:\n"
    "                                    logger.info(\n"
    "                                        f'[FX18-3] {market} MTF DOWN이나 '\n"
    "                                        f'BULL+SCR={_fx18_scr:.1f}% ≥ 15% → MTF 차단 우회'\n"
    "                                    )\n"
    "                                else:\n"
    "                                    logger.info(\n"
    "                                        f\" MTF  ({market}): \"\n"
    "                                        f\"방향={_mtf_result.final_direction.name} | \"\n"
    "                                        f\"{_mtf_result.reason}\"\n"
    "                                    )\n"
    "                                    return"
)

if "if _mtf_dir <= -1 and not _is_bear_rev:" in src:
    src = src.replace(OLD18_3, NEW18_3, 1)
    if NEW18_3[:50] in src:
        with open(BUY_F, "w", encoding="utf-8") as f:
            f.write(src)
        results.append(("FX18-3", "✅", "engine_buy.py Surge MTF 바이패스 삽입 완료"))
    else:
        # 멀티라인 패턴 정밀 삽입 실패 시 단순 라인 기반 삽입
        lines = src.splitlines()
        new_lines = []
        i = 0
        inserted = False
        while i < len(lines):
            line = lines[i]
            if "if _mtf_dir <= -1 and not _is_bear_rev:" in line and not inserted:
                indent = len(line) - len(line.lstrip())
                pad = " " * indent
                new_lines.append(line)
                new_lines.append(f"{pad}    # [FX18-3] Surge ≥15% 시 MTF DOWN 차단 우회")
                new_lines.append(f"{pad}    _fx18_scr = 0.0")
                new_lines.append(f"{pad}    _scr_c = getattr(self, '_scr_cache', {{}})")
                new_lines.append(f"{pad}    _fx18_scr = float(_scr_c.get(market, {{}}).get('scr', 0.0))")
                new_lines.append(f"{pad}    if _fx18_scr == 0.0:")
                new_lines.append(f"{pad}        _mcr = getattr(self, '_market_change_rates', {{}})")
                new_lines.append(f"{pad}        _fx18_scr = float(_mcr.get(market, 0.0)) * 100")
                new_lines.append(f"{pad}    _fx18_gr = str(getattr(getattr(self, '_global_regime', None), 'value', 'UNKNOWN') or 'UNKNOWN').upper()")
                new_lines.append(f"{pad}    _fx18_bull = _fx18_gr in ('BULL', 'TRENDING_UP', 'RECOVERY')")
                new_lines.append(f"{pad}    if _fx18_bull and _fx18_scr >= 15.0:")
                new_lines.append(f"{pad}        logger.info(f'[FX18-3] {{market}} BULL+SCR={{_fx18_scr:.1f}}% ≥ 15% → MTF 차단 우회')")
                new_lines.append(f"{pad}    else:")
                inserted = True
            else:
                new_lines.append(line)
            i += 1
        if inserted:
            with open(BUY_F, "w", encoding="utf-8") as f:
                f.write("\n".join(new_lines))
            results.append(("FX18-3", "✅", "engine_buy.py Surge MTF 바이패스 라인 삽입 완료"))
        else:
            results.append(("FX18-3", "⚠️", "_mtf_dir 패턴 미발견 — 수동 확인"))
else:
    results.append(("FX18-3", "⚠️", "engine_buy.py _mtf_dir 패턴 미발견"))

# FX18-3 컴파일 검증
try:
    py_compile.compile(BUY_F, doraise=True)
    results.append(("FX18-3-compile", "✅", "engine_buy.py 컴파일 성공"))
except py_compile.PyCompileError as e:
    results.append(("FX18-3-compile", "❌", str(e)))

# ── 결과 출력 ─────────────────────────────────────────────────────────────
print("\n=== FX18 패치 결과 ===")
print(f"{'ID':<20} {'상태':<5} 내용")
print("-" * 70)
for rid, st, msg in results:
    print(f"{rid:<20} {st:<5} {msg}")
print(f"\n백업 위치: {bak}")
print("\n실행 명령:")
print("  git add -A")
print('  git commit -m "fix: FX18-1/3 MTFMerger RSI확장/Surge MTF바이패스"')
print("  git push origin main")
print("  taskkill /F /IM python.exe /T")
print("  python main.py --mode paper")
