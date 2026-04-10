# fix_cache_and_ws.py
"""FIX-1: CacheManager get_ohlcv / get_candles   
FIX-2: _analyze_existing_position get_ohlcv    
FIX-3: WebSocket orderbook  set_orderbook     
FIX-4: WebSocket subscribe_orderbook()"""
import shutil, py_compile
from pathlib import Path

# ── 파일 경로 ─────────────────────────────────────────────────────────────────
CACHE_FILE  = Path("data/storage/cache_manager.py")
ENGINE_FILE = Path("core/engine.py")

shutil.copy(CACHE_FILE,  CACHE_FILE.with_suffix(".py.bak_cache2"))
shutil.copy(ENGINE_FILE, ENGINE_FILE.with_suffix(".py.bak_fix4"))
print("  ")

# ══════════════════════════════════════════════════════════════════════════════
# FIX-1: CacheManager에 get_ohlcv / get_candles 래퍼 추가
# ══════════════════════════════════════════════════════════════════════════════
cache_text = CACHE_FILE.read_text(encoding="utf-8", errors="ignore")

OHLCV_WRAPPER = '''
    # ── OHLCV 래퍼 (NpyCache 위임) ─────────────────────────────────────────
    def get_ohlcv(self, market: str, interval: str = "1h") -> Optional[object]:
        """NpyCache OHLCV DataFrame .  None."""
        try:
            if hasattr(self, 'npy_cache') and self.npy_cache is not None:
                return self.npy_cache.get(market, interval)
        except Exception:
            pass
        return None

    def get_candles(self, market: str, interval: str = "1h") -> Optional[object]:
        """get_ohlcv  ( )."""
        return self.get_ohlcv(market, interval)

    def set_ohlcv(self, market: str, interval: str, df) -> None:
        """NpyCache OHLCV DataFrame ."""
        try:
            if hasattr(self, 'npy_cache') and self.npy_cache is not None:
                self.npy_cache.set(market, interval, df)
        except Exception:
            pass
'''

# get_stats 메서드 바로 앞에 삽입 (마지막 def 위치 활용)
if "def get_ohlcv" in cache_text:
    print("  FIX-1: get_ohlcv   – ")
else:
    # get_stats def 바로 앞에 삽입
    target = "    def get_stats(self)"
    if target in cache_text:
        cache_text = cache_text.replace(target, OHLCV_WRAPPER + "\n" + target, 1)
        print(" FIX-1: get_ohlcv / get_candles   ")
    else:
        # fallback: 파일 끝에 추가
        cache_text += "\n" + OHLCV_WRAPPER
        print(" FIX-1: get_ohlcv / get_candles    ")

CACHE_FILE.write_text(cache_text, encoding="utf-8")

# 문법 검사
try:
    py_compile.compile(str(CACHE_FILE), doraise=True)
    print(" cache_manager.py  OK")
except py_compile.PyCompileError as e:
    print(f" cache_manager.py  : {e}")
    shutil.copy(CACHE_FILE.with_suffix(".py.bak_cache2"), CACHE_FILE)
    print(" cache_manager.py  ")
    exit(1)

# ══════════════════════════════════════════════════════════════════════════════
# FIX-2 + FIX-3 + FIX-4: engine.py 수정
# ══════════════════════════════════════════════════════════════════════════════
engine_text = ENGINE_FILE.read_text(encoding="utf-8", errors="ignore")
engine_lines = engine_text.splitlines()

# ── FIX-2: _analyze_existing_position의 get_ohlcv 호출 수정 ──────────────────
# rest_collector.get_ohlcv → cache_manager.get_ohlcv 또는 직접 REST 호출로 변경
OLD_CANDLE = "            candles = self.cache_manager.get_ohlcv(market, \"1h\")"
NEW_CANDLE = '''\
            # NpyCache 우선, 없으면 REST로 직접 수집
            candles = self.cache_manager.get_ohlcv(market, "1h")
            if candles is None or (hasattr(candles, '__len__') and len(candles) < 20):
                try:
                    candles = await self.rest_collector.get_ohlcv(market, interval="1h", count=100)
                except Exception:
                    candles = None'''

if OLD_CANDLE in engine_text:
    engine_text = engine_text.replace(OLD_CANDLE, NEW_CANDLE, 1)
    print(" FIX-2: _analyze_existing_position    ")
else:
    # 유사 패턴 탐색
    fixed = False
    new_lines = []
    for ln in engine_text.splitlines():
        if "cache_manager.get_ohlcv" in ln and "_analyze_existing_position" not in ln:
            new_lines.append(ln)
        elif "cache_manager.get_ohlcv" in ln:
            new_lines.append(NEW_CANDLE)
            fixed = True
        else:
            new_lines.append(ln)
    if fixed:
        engine_text = "\n".join(new_lines)
        print(" FIX-2: fallback    ")
    else:
        print("  FIX-2:   –    ( )")

# ── FIX-3: WebSocket orderbook 핸들러에 set_orderbook 호출 보장 ───────────────
# L330 이후에 set_orderbook 호출이 있는지 확인
if "self.cache_manager.set_orderbook" not in engine_text:
    # orderbook elif 블록 끝에 삽입
    OLD_OB_BLOCK = '                elif msg_type == \'orderbook\':\n                    if market:'
    NEW_OB_BLOCK = '''\
                elif msg_type == 'orderbook':
                    if market:
                        # 즉시 캐시 저장 (OrderBookAnalyzer용)
                        self.cache_manager.set_orderbook(market, data)
                        logger.debug(f"   | {market}")'''
    if OLD_OB_BLOCK in engine_text:
        engine_text = engine_text.replace(OLD_OB_BLOCK, NEW_OB_BLOCK, 1)
        print(" FIX-3: set_orderbook   ")
    else:
        # 더 유연한 탐색
        lines_list = engine_text.splitlines()
        for idx, line in enumerate(lines_list):
            if "msg_type == 'orderbook'" in line or 'msg_type == "orderbook"' in line:
                indent = len(line) - len(line.lstrip())
                inner  = " " * (indent + 4)
                insert_line = f'{inner}self.cache_manager.set_orderbook(market, data)'
                log_line    = f'{inner}logger.debug(f"   | {{market}}")'
                lines_list.insert(idx + 2, log_line)
                lines_list.insert(idx + 2, insert_line)
                engine_text = "\n".join(lines_list)
                print(" FIX-3: fallback set_orderbook  ")
                break
        else:
            print("  FIX-3: orderbook   –   ")
else:
    print("  FIX-3: set_orderbook   – ")

# ── FIX-4: subscribe_orderbook() 호출 확인 ────────────────────────────────────
if "subscribe_orderbook" not in engine_text:
    old_ticker = "self.ws_collector.subscribe_ticker()"
    new_ticker = "self.ws_collector.subscribe_ticker()\n            self.ws_collector.subscribe_orderbook()"
    if old_ticker in engine_text:
        engine_text = engine_text.replace(old_ticker, new_ticker, 1)
        print(" FIX-4: subscribe_orderbook() 추가 완료")
    else:
        print("  FIX-4: subscribe_ticker  –   ")
else:
    print("  FIX-4: subscribe_orderbook   – ")

ENGINE_FILE.write_text(engine_text, encoding="utf-8")

# ── 최종 문법 검사 ────────────────────────────────────────────────────────────
try:
    py_compile.compile(str(ENGINE_FILE), doraise=True)
    print("\n engine.py  OK –   ")
    print("   : python start_paper.py")
except py_compile.PyCompileError as e:
    # 오류 주변 라인 출력
    import re
    m = re.search(r'line (\d+)', str(e))
    if m:
        err_line = int(m.group(1))
        lines_err = ENGINE_FILE.read_text(encoding="utf-8").splitlines()
        print(f"\n   (L{err_line}): {e}")
        for i in range(max(0, err_line-3), min(len(lines_err), err_line+3)):
            print(f"  L{i+1}: {lines_err[i]}")
    shutil.copy(ENGINE_FILE.with_suffix(".py.bak_fix4"), ENGINE_FILE)
    print(" engine.py   ")
    exit(1)
