# fix_candle_final.py
"""FIX-1: RestCollector.get_ohlcv    _analyze_existing_position  
FIX-2: _initial_data_fetch    NpyCache"""
import shutil, py_compile, re, subprocess, sys
from pathlib import Path

ENGINE = Path("core/engine.py")
shutil.copy(ENGINE, ENGINE.with_suffix(".py.bak_candle"))
print("  ")

# ── Step 1: RestCollector.get_ohlcv 실제 시그니처 확인 ────────────────────────
result = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0,'.');"
     "from data.collectors.rest_collector import RestCollector;"
     "import inspect;"
     "print(inspect.signature(RestCollector.get_ohlcv))"],
    capture_output=True, text=True
)
sig = result.stdout.strip()
print(f" RestCollector.get_ohlcv : {sig}")

# 시그니처에서 파라미터 파악
# 예상: (self, market, interval='1h', count=200) 또는 (self, market, timeframe, count)
if "timeframe" in sig:
    rest_call = 'await self.rest_collector.get_ohlcv(market, "1h", 100)'
elif "interval" in sig and "count" in sig:
    rest_call = 'await self.rest_collector.get_ohlcv(market, interval="1h", count=100)'
elif "interval" in sig:
    rest_call = 'await self.rest_collector.get_ohlcv(market, interval="1h")'
elif sig and "(" in sig:
    # 파라미터 목록 파싱
    params = [p.strip().split('=')[0].strip() for p in sig.strip('()').split(',')]
    params = [p for p in params if p and p != 'self']
    if len(params) >= 2:
        rest_call = f'await self.rest_collector.get_ohlcv(market, "1h", 100)'
    else:
        rest_call = 'await self.rest_collector.get_ohlcv(market)'
else:
    rest_call = 'await self.rest_collector.get_ohlcv(market, "1h", 100)'

print(f" REST : {rest_call}")

# ── Step 2: engine.py 수정 ────────────────────────────────────────────────────
text = ENGINE.read_text(encoding="utf-8", errors="ignore")

# _analyze_existing_position 내부 REST fallback 수정
# 기존 패턴: await self.rest_collector.get_ohlcv(market, interval="1h", count=100)
old_rest = re.compile(
    r'candles = await self\.rest_collector\.get_ohlcv\([^)]+\)',
    re.MULTILINE
)
new_rest = f"candles = {rest_call}"

if old_rest.search(text):
    text = old_rest.sub(new_rest, text, count=1)
    print(" FIX-1: REST fallback   ")
else:
    print("  FIX-1: REST fallback   –  ")
    # _analyze_existing_position 내 candles = None 이후에 삽입
    text = text.replace(
        "                except Exception:\n                    candles = None",
        f"                except Exception:\n                    candles = None\n            # REST 직접 호출 (fallback)\n            if candles is None or (hasattr(candles,'__len__') and len(candles)<20):\n                try:\n                    candles = {rest_call}\n                except Exception:\n                    candles = None",
        1
    )

# ── Step 3: _initial_data_fetch 후 NpyCache 저장 추가 ────────────────────────
# _initial_data_fetch에서 캔들을 수집하지만 NpyCache에 저장하지 않는 문제 수정
FETCH_SAVE = '''
    async def _save_initial_candles(self):
        """NpyCache"""
        markets = self.settings.trading.target_markets
        saved = 0
        for market in markets:
            try:
                df = await self.rest_collector.get_ohlcv(market, ''' + ('"1h", 200' if 'interval' not in sig else 'interval="1h", count=200') + ''')
                if df is not None and len(df) > 0:
                    self.cache_manager.set_ohlcv(market, "1h", df)
                    saved += 1
                    logger.debug(f"   | {market} | {len(df)}개")
            except Exception as e:
                logger.debug(f"   ({market}): {e}")
        logger.info(f"   NpyCache   | {saved}/{len(markets)}개 코인")
'''

if "_save_initial_candles" not in text:
    # _initial_data_fetch 정의 바로 앞에 삽입
    text = text.replace(
        "    async def _initial_data_fetch(",
        FETCH_SAVE + "\n    async def _initial_data_fetch(",
        1
    )
    print(" FIX-2: _save_initial_candles   ")
else:
    print("  FIX-2: _save_initial_candles  ")

# _initial_data_fetch 완료 로그 직후에 _save_initial_candles 호출 추가
if "await self._save_initial_candles()" not in text:
    text = text.replace(
        "logger.info(f\"    ",
        "await self._save_initial_candles()\n            logger.info(f\"    ",
        1
    )
    # 인코딩 문제로 못 찾을 경우 fallback
    if "await self._save_initial_candles()" not in text:
        # _initial_data_fetch 호출 직후에 삽입
        text = text.replace(
            "await self._initial_data_fetch()",
            "await self._initial_data_fetch()\n            await self._save_initial_candles()",
            1
        )
    print(" FIX-3:        ")
else:
    print("  FIX-3: _save_initial_candles   ")

ENGINE.write_text(text, encoding="utf-8")

# ── 문법 검사 ─────────────────────────────────────────────────────────────────
try:
    py_compile.compile(str(ENGINE), doraise=True)
    print("\n engine.py  OK –   ")
    print("   : python start_paper.py")
except py_compile.PyCompileError as e:
    m = re.search(r'line (\d+)', str(e))
    if m:
        err_line = int(m.group(1))
        err_lines = ENGINE.read_text(encoding="utf-8").splitlines()
        print(f"\n   (L{err_line}): {e}")
        for idx in range(max(0, err_line-4), min(len(err_lines), err_line+4)):
            print(f"  L{idx+1}: {err_lines[idx]}")
    shutil.copy(ENGINE.with_suffix(".py.bak_candle"), ENGINE)
    print(" engine.py   ")
    exit(1)
