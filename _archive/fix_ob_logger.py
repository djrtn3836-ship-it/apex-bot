"""fix_ob_logger.py
– engine.py OrderBookAnalyzer   loguru logger 
– orderbook_analyzer.py  loguru"""
import shutil, py_compile
from pathlib import Path

# ── 1. engine.py logger.info → loguru 확인 및 수정 ──────────────────────
engine_path = Path("core/engine.py")
shutil.copy(engine_path, "core/engine.py.bak_logger")
text = engine_path.read_text(encoding="utf-8", errors="ignore")

# 현재 삽입된 블록의 단순 문자열 교체 (인코딩 무관)
OLD = "logger.info('??OrderBookAnalyzer ???')"
NEW = "logger.info(' OrderBookAnalyzer   ( )')"

OLD2 = "logger.error(f'??OrderBookAnalyzer ???: {_ob_err}')"
NEW2 = "logger.error(f' OrderBookAnalyzer  : {_ob_err}')"

changed = 0
if OLD in text:
    text = text.replace(OLD, NEW, 1)
    changed += 1
    print("    ")
else:
    # 깨진 문자열 없이 이미 정상인 경우
    if "OrderBookAnalyzer 초기화 완료" in text:
        print("     ")
        changed += 1
    else:
        # L194 직접 교체
        lines = text.splitlines()
        for i, ln in enumerate(lines):
            if "OrderBookAnalyzer" in ln and "logger.info" in ln and i in range(190, 200):
                lines[i] = "            logger.info(' OrderBookAnalyzer   ( )')"
                changed += 1
                print(f" L{i+1}  ")
                break
        text = "\n".join(lines)

if OLD2 in text:
    text = text.replace(OLD2, NEW2, 1)
    print("   ")

engine_path.write_text(text, encoding="utf-8")
try:
    py_compile.compile(str(engine_path), doraise=True)
    print(" engine.py  OK")
except py_compile.PyCompileError as e:
    print(f"  : {e}")
    shutil.copy("core/engine.py.bak_logger", engine_path)
    print("  ")

# ── 2. orderbook_analyzer.py – loguru로 통일 ────────────────────────────
ob_path = Path("data/processors/orderbook_analyzer.py")
ob_text = ob_path.read_text(encoding="utf-8", errors="ignore")

if "import logging" in ob_text and "from loguru" not in ob_text:
    ob_text = ob_text.replace(
        "import logging\n\nlogger = logging.getLogger(__name__)",
        "from loguru import logger"
    )
    # fallback
    if "import logging" in ob_text:
        ob_text = ob_text.replace("import logging", "from loguru import logger  # loguru 통일")
        ob_text = ob_text.replace("logger = logging.getLogger(__name__)", "")
    ob_path.write_text(ob_text, encoding="utf-8")
    try:
        py_compile.compile(str(ob_path), doraise=True)
        print(" orderbook_analyzer.py loguru  ")
    except py_compile.PyCompileError as e:
        print(f" orderbook_analyzer.py  : {e}")
else:
    print(" orderbook_analyzer.py  loguru  ")

print("\n   → python start_paper.py")
