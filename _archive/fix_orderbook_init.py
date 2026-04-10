"""fix_orderbook_init.py
 - self.orderbook_analyzer  engine.py __init__ 
 -     bypass"""
import shutil, py_compile, re
from pathlib import Path

engine_path = Path("core/engine.py")
shutil.copy(engine_path, "core/engine.py.bak_ob")
text = engine_path.read_text(encoding="utf-8", errors="ignore")
lines = text.splitlines()

# ── FIX 1: __init__에 orderbook_analyzer 초기화 삽입 ──────────────────────
# volume_spike 초기화 라인 바로 뒤에 삽입
INSERT_AFTER = "self.volume_spike"
ob_init_block = (
    "\n        # ✅ OrderBook 분석기 초기화\n"
    "        try:\n"
    "            from data.processors.orderbook_analyzer import OrderBookAnalyzer\n"
    "            self.orderbook_analyzer = OrderBookAnalyzer()\n"
    "            logger.info(' OrderBookAnalyzer  ')\n"
    "        except Exception as _e:\n"
    "            self.orderbook_analyzer = None\n"
    "            logger.warning(f' OrderBookAnalyzer  : {_e}')\n"
)

fix1_done = False
new_lines = []
for i, line in enumerate(lines):
    new_lines.append(line)
    if not fix1_done and INSERT_AFTER in line and "self." in line and "=" in line:
        # volume_spike 할당 라인 뒤에 블록 삽입
        new_lines.append(ob_init_block)
        fix1_done = True

if not fix1_done:
    print(" FIX-1: volume_spike    .")
    print("  →    :")
    print("    Select-String -Path core\\engine.py -Pattern 'volume_spike' | Select-Object LineNumber, Line")
else:
    print(" FIX-1: orderbook_analyzer    ")

# ── FIX 2: 피라미딩 전용 덤핑 bypass ─────────────────────────────────────
# _check_position_exits 내 피라미딩 실행 직전에 덤핑 override 플래그 삽입
# 기존 패턴:
#   is_dumping, dump_reason = self.volume_spike.is_dumping(...)
#   ...
#   if is_dumping and not _is_bear_rev:
#       return
DUMP_BLOCK_OLD = (
    "            if is_dumping and not _is_bear_rev:\n"
    "                # BEAR_REVERSAL "
)
DUMP_BLOCK_NEW = (
    "            _in_pyramid = getattr(self, '_current_pyramid_market', None) == market\n"
    "            if is_dumping and not _is_bear_rev and not _in_pyramid:\n"
    "                # BEAR_REVERSAL "
)

text2 = "\n".join(new_lines)
if DUMP_BLOCK_OLD in text2:
    text2 = text2.replace(DUMP_BLOCK_OLD, DUMP_BLOCK_NEW, 1)
    print(" FIX-2:   bypass   ")
else:
    # 인코딩 깨진 경우를 고려한 라인 번호 기반 패치
    line_list = text2.splitlines()
    for idx, ln in enumerate(line_list):
        if "is_dumping and not _is_bear_rev" in ln and "return" in line_list[idx + 1]:
            indent = len(ln) - len(ln.lstrip())
            sp = " " * indent
            line_list[idx] = (
                f"{sp}_in_pyramid = getattr(self, '_current_pyramid_market', None) == market\n"
                f"{sp}if is_dumping and not _is_bear_rev and not _in_pyramid:"
            )
            text2 = "\n".join(line_list)
            print(" FIX-2 (fallback): 덤핑 bypass 조건 삽입 완료")
            break
    else:
        print(" FIX-2:     –   ")

# ── FIX 3: _check_position_exits 내 피라미딩 진입 시 마커 설정 ──────────
# ExecutionRequest 생성 직전에 self._current_pyramid_market = market 삽입
PYRAMID_MARKER_PATTERN = "M4_피라미딩"
pyr_found = False
out_lines = text2.splitlines()
for idx, ln in enumerate(out_lines):
    if PYRAMID_MARKER_PATTERN in ln and "reason" in ln:
        indent = len(ln) - len(ln.lstrip())
        sp = " " * indent
        out_lines.insert(idx, f"{sp}self._current_pyramid_market = market  # 피라미딩 마커")
        pyr_found = True
        break
if pyr_found:
    print(" FIX-3:  (self._current_pyramid_market) 삽입 완료")
    text2 = "\n".join(out_lines)
else:
    print(" FIX-3:  reason   ")

# ── 저장 및 컴파일 검증 ───────────────────────────────────────────────────
engine_path.write_text(text2, encoding="utf-8")
try:
    py_compile.compile(str(engine_path), doraise=True)
    print("\n engine.py  OK –  ")
    print("   : python start_paper.py")
except py_compile.PyCompileError as e:
    print(f"\n  : {e}")
    shutil.copy("core/engine.py.bak_ob", engine_path)
    print("   ")
