# fix_final.py
"""
FIX-1: cache_manager.py  get_ohlcv 래퍼 → self._npy_cache 사용 + REST fallback
FIX-2: engine.py _main_loop → 기존 9개 포지션 전체 _analyze_existing_position 호출
"""
import shutil, py_compile, re
from pathlib import Path

CACHE  = Path("data/storage/cache_manager.py")
ENGINE = Path("core/engine.py")

shutil.copy(CACHE,  CACHE.with_suffix(".py.bak_final"))
shutil.copy(ENGINE, ENGINE.with_suffix(".py.bak_final"))
print("📦 백업 완료")

# ══════════════════════════════════════════════════════════════════
# FIX-1: cache_manager.py  get_ohlcv 래퍼 올바른 속성명으로 교체
# ══════════════════════════════════════════════════════════════════
cache_text = CACHE.read_text(encoding="utf-8", errors="ignore")

# 기존 잘못된 래퍼 제거 후 올바른 버전으로 교체
OLD_WRAPPER_PATTERN = r"    # ── OHLCV 래퍼.*?def set_ohlcv.*?pass\n"
NEW_WRAPPER = '''    # ── OHLCV 래퍼 (NpyCache 위임) ─────────────────────────────────
    def get_ohlcv(self, market: str, interval: str = "1h"):
        """NpyCache에서 OHLCV DataFrame 반환. 없으면 None."""
        try:
            npy = getattr(self, '_npy_cache', None)
            if npy is not None:
                # load() 또는 get() 메서드 자동 감지
                if hasattr(npy, 'load'):
                    df = npy.load(market, interval)
                elif hasattr(npy, 'get'):
                    df = npy.get(market, interval)
                else:
                    df = None
                if df is not None and len(df) > 0:
                    return df
        except Exception:
            pass
        return None

    def get_candles(self, market: str, interval: str = "1h"):
        """get_ohlcv 별칭 (하위 호환)."""
        return self.get_ohlcv(market, interval)

    def set_ohlcv(self, market: str, interval: str, df) -> None:
        """NpyCache에 OHLCV DataFrame 저장."""
        try:
            npy = getattr(self, '_npy_cache', None)
            if npy is not None:
                if hasattr(npy, 'save'):
                    npy.save(market, interval, df)
                elif hasattr(npy, 'set'):
                    npy.set(market, interval, df)
        except Exception:
            pass

'''

# 기존 래퍼(잘못된 버전) 교체
cleaned = re.sub(OLD_WRAPPER_PATTERN, '', cache_text, flags=re.DOTALL)

# def get_ohlcv / get_candles / set_ohlcv 블록 제거 후 재삽입
lines_c = cleaned.splitlines()
result_lines = []
skip = False
for ln in lines_c:
    if re.match(r'\s+def (get_ohlcv|get_candles|set_ohlcv)\(', ln):
        skip = True
    if skip and re.match(r'\s+def [^(]+\(', ln) and 'get_ohlcv' not in ln \
            and 'get_candles' not in ln and 'set_ohlcv' not in ln:
        skip = False
    if not skip:
        result_lines.append(ln)

cleaned = "\n".join(result_lines)

# get_stats 바로 앞에 새 래퍼 삽입
if "    def get_stats(self)" in cleaned:
    cleaned = cleaned.replace(
        "    def get_stats(self)",
        NEW_WRAPPER + "    def get_stats(self)",
        1
    )
    print("✅ FIX-1: get_ohlcv 래퍼 교체 완료 (_npy_cache 사용)")
else:
    cleaned += "\n" + NEW_WRAPPER
    print("✅ FIX-1: get_ohlcv 래퍼 파일 끝에 추가 완료")

CACHE.write_text(cleaned, encoding="utf-8")

try:
    py_compile.compile(str(CACHE), doraise=True)
    print("✅ cache_manager.py 문법 OK")
except py_compile.PyCompileError as e:
    print(f"❌ cache_manager.py 오류: {e}")
    shutil.copy(CACHE.with_suffix(".py.bak_final"), CACHE)
    exit(1)

# ══════════════════════════════════════════════════════════════════
# FIX-2: engine.py _main_loop 전체 코인 분석 확인 및 수정
# ══════════════════════════════════════════════════════════════════
engine_text = ENGINE.read_text(encoding="utf-8", errors="ignore")
engine_lines = engine_text.splitlines()

# _main_loop 내부 구조 파악: asyncio.gather 또는 for market 루프 위치
main_loop_start = None
for i, ln in enumerate(engine_lines):
    if "async def _main_loop" in ln:
        main_loop_start = i
        break

if main_loop_start is None:
    print("❌ _main_loop 위치를 찾지 못했습니다.")
    exit(1)

# _main_loop 내부에서 _analyze_existing_position 호출 여부 확인
loop_body = "\n".join(engine_lines[main_loop_start:main_loop_start+120])

if "_analyze_existing_position" not in loop_body:
    print("⚠️  _main_loop에 _analyze_existing_position 호출 없음 → 삽입")

    # asyncio.gather 또는 for market in self.markets 패턴 찾기
    gather_idx = None
    for i in range(main_loop_start, min(main_loop_start+120, len(engine_lines))):
        ln = engine_lines[i]
        if "asyncio.gather" in ln or ("for market in" in ln and "self." in ln):
            gather_idx = i
            break

    if gather_idx:
        indent = len(engine_lines[gather_idx]) - len(engine_lines[gather_idx].lstrip())
        pad = " " * indent
        # gather 호출 바로 앞에 기존 포지션 분석 블록 삽입
        existing_block = [
            f"{pad}# ── 기존 포지션 ML 재평가 (전체 코인) ──────────────────",
            f"{pad}existing_tasks = [",
            f"{pad}    self._analyze_existing_position(m)",
            f"{pad}    for m in self.settings.trading.target_markets",
            f"{pad}    if self.portfolio.is_position_open(m)",
            f"{pad}]",
            f"{pad}if existing_tasks:",
            f"{pad}    await asyncio.gather(*existing_tasks, return_exceptions=True)",
            f"{pad}",
        ]
        for j, block_ln in enumerate(existing_block):
            engine_lines.insert(gather_idx + j, block_ln)
        print(f"✅ FIX-2: 기존 포지션 분석 블록 삽입 완료 (L{gather_idx})")
    else:
        # fallback: _main_loop 시작 직후 while True: 내부에 삽입
        for i in range(main_loop_start, min(main_loop_start+30, len(engine_lines))):
            if "while True" in engine_lines[i] or "while self._running" in engine_lines[i]:
                indent = (len(engine_lines[i]) - len(engine_lines[i].lstrip())) + 4
                pad = " " * indent
                insert_block = [
                    f"{pad}# ── 기존 포지션 ML 재평가 ──────────────────────────",
                    f"{pad}existing_tasks = [",
                    f"{pad}    self._analyze_existing_position(m)",
                    f"{pad}    for m in self.settings.trading.target_markets",
                    f"{pad}    if self.portfolio.is_position_open(m)",
                    f"{pad}]",
                    f"{pad}if existing_tasks:",
                    f"{pad}    await asyncio.gather(*existing_tasks, return_exceptions=True)",
                    f"{pad}",
                ]
                for j, block_ln in enumerate(insert_block):
                    engine_lines.insert(i + 1 + j, block_ln)
                print(f"✅ FIX-2: fallback 포지션 분석 블록 삽입 완료 (L{i})")
                break
else:
    print("⚠️  FIX-2: _analyze_existing_position 이미 존재 → 내용 검증")
    # 속성명 오류(_npy_cache vs npy_cache)만 있을 수 있으므로 FIX-1로 충분

engine_text = "\n".join(engine_lines)

# ── _analyze_existing_position 내부 get_ohlcv 호출도 REST fallback 포함으로 교체 ──
OLD_CANDLE_BLOCK = '''\
            # NpyCache 우선, 없으면 REST로 직접 수집
            candles = self.cache_manager.get_ohlcv(market, "1h")
            if candles is None or (hasattr(candles, '__len__') and len(candles) < 20):
                try:
                    candles = await self.rest_collector.get_ohlcv(market, interval="1h", count=100)
                except Exception:
                    candles = None'''

if OLD_CANDLE_BLOCK not in engine_text:
    # 단순 get_ohlcv 호출 → REST fallback 포함으로 교체
    engine_text = engine_text.replace(
        '            candles = self.cache_manager.get_ohlcv(market, "1h")',
        OLD_CANDLE_BLOCK,
        1
    )
    print("✅ FIX-2b: REST fallback 캔들 조회 삽입 완료")
else:
    print("⚠️  FIX-2b: REST fallback 이미 존재")

ENGINE.write_text(engine_text, encoding="utf-8")

try:
    py_compile.compile(str(ENGINE), doraise=True)
    print("\n✅ engine.py 문법 OK – 모든 수정 완료")
    print("   다음: python start_paper.py")
except py_compile.PyCompileError as e:
    m = re.search(r'line (\d+)', str(e))
    if m:
        err_line = int(m.group(1))
        err_lines = ENGINE.read_text(encoding="utf-8").splitlines()
        print(f"\n❌ 문법 오류 (L{err_line}): {e}")
        for idx in range(max(0, err_line-4), min(len(err_lines), err_line+4)):
            print(f"  L{idx+1}: {err_lines[idx]}")
    shutil.copy(ENGINE.with_suffix(".py.bak_final"), ENGINE)
    print("🔄 engine.py 원본 복구 완료")
    exit(1)
