# fix_candle2.py
import shutil, py_compile, re
from pathlib import Path

ENGINE = Path("core/engine.py")
shutil.copy(ENGINE, ENGINE.with_suffix(".py.bak_candle2"))

lines = ENGINE.read_text(encoding="utf-8", errors="ignore").splitlines()

# ── FIX-1: RestCollector 시그니처 확인 (minute60 사용) ───────────────────────
# 실제 시그니처: (self, market, interval='minute60', count=200)
# '1h' 대신 'minute60' 사용해야 함

# ── FIX-2: _save_initial_candles 메서드 추가 ─────────────────────────────────
SAVE_METHOD = [
    "    async def _save_initial_candles(self):",
    "        \"\"\"초기 캔들 데이터를 NpyCache에 저장\"\"\"",
    "        markets = self.settings.trading.target_markets",
    "        saved = 0",
    "        for market in markets:",
    "            try:",
    "                df = await self.rest_collector.get_ohlcv(market, interval='minute60', count=200)",
    "                if df is not None and len(df) > 0:",
    "                    self.cache_manager.set_ohlcv(market, '1h', df)",
    "                    saved += 1",
    "                    logger.debug(f'   | {market} | {len(df)}개')",
    "            except Exception as e:",
    "                logger.debug(f'   ({market}): {e}')",
    "        logger.info(f'   NpyCache   | {saved}/{len(markets)}개 코인')",
    "",
]

# ── FIX-3: _analyze_existing_position REST fallback도 'minute60'으로 수정 ─────
for i, ln in enumerate(lines):
    if "rest_collector.get_ohlcv" in ln and "_analyze_existing" not in ln:
        # 기존 interval="1h" → interval='minute60'
        lines[i] = ln.replace('interval="1h"', "interval='minute60'").replace("interval='1h'", "interval='minute60'")

# ── 삽입 위치: _initial_data_fetch 정의 바로 앞 ───────────────────────────────
insert_idx = None
for i, ln in enumerate(lines):
    if "async def _initial_data_fetch(" in ln:
        insert_idx = i
        break

if insert_idx is None:
    print(" _initial_data_fetch   .")
    exit(1)

if any("async def _save_initial_candles" in ln for ln in lines):
    print("  _save_initial_candles   –  ")
else:
    for j, method_line in enumerate(SAVE_METHOD):
        lines.insert(insert_idx + j, method_line)
    print(f" FIX-2: _save_initial_candles   (L{insert_idx})")

# ── FIX-4: _initial_data_fetch 완료 로그 직후에 호출 삽입 ────────────────────
# 삽입 위치 재탐색 (메서드 삽입으로 라인 번호 변경됨)
target_idx = None
for i, ln in enumerate(lines):
    if "초기 데이터 수집 완료" in ln or ("initial_data_fetch" in ln and "success" in ln):
        target_idx = i
        break

if target_idx is None:
    # 한글 인코딩 문제로 못 찾을 경우 _initial_data_fetch 내부 logger.info 탐색
    in_fetch = False
    for i, ln in enumerate(lines):
        if "async def _initial_data_fetch(" in ln:
            in_fetch = True
        if in_fetch and "logger.info" in ln and "success" in ln:
            target_idx = i
            break
        if in_fetch and "async def " in ln and "_initial_data_fetch" not in ln:
            break

if target_idx:
    indent = len(lines[target_idx]) - len(lines[target_idx].lstrip())
    pad = " " * indent
    call_line = f"{pad}await self._save_initial_candles()"
    if "await self._save_initial_candles()" not in "\n".join(lines[target_idx-2:target_idx+2]):
        lines.insert(target_idx, call_line)
        print(f" FIX-4: _save_initial_candles    (L{target_idx})")
    else:
        print("  FIX-4:   ")
else:
    print("  FIX-4:     – L349 _initial_data_fetch   ")
    for i, ln in enumerate(lines):
        if "await self._initial_data_fetch()" in ln:
            indent = len(ln) - len(ln.lstrip())
            pad = " " * indent
            lines.insert(i + 1, f"{pad}await self._save_initial_candles()")
            print(f" FIX-4 fallback: L{i+1}  ")
            break

# ── 저장 ─────────────────────────────────────────────────────────────────────
ENGINE.write_text("\n".join(lines), encoding="utf-8")

try:
    py_compile.compile(str(ENGINE), doraise=True)
    print("\n engine.py  OK –   ")
    print("   : python start_paper.py")
except py_compile.PyCompileError as e:
    import re as _re
    m = _re.search(r'line (\d+)', str(e))
    if m:
        err_line = int(m.group(1))
        err_lines = ENGINE.read_text(encoding="utf-8").splitlines()
        print(f"\n   (L{err_line}): {e}")
        for idx in range(max(0, err_line-4), min(len(err_lines), err_line+4)):
            print(f"  L{idx+1}: {err_lines[idx]}")
    shutil.copy(ENGINE.with_suffix(".py.bak_candle2"), ENGINE)
    print(" engine.py   ")
    exit(1)
