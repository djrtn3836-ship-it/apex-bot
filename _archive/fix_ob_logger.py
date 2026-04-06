"""
fix_ob_logger.py
– engine.py의 OrderBookAnalyzer 초기화 로그를 loguru logger로 교체
– orderbook_analyzer.py 내부도 loguru로 통일
"""
import shutil, py_compile
from pathlib import Path

# ── 1. engine.py logger.info → loguru 확인 및 수정 ──────────────────────
engine_path = Path("core/engine.py")
shutil.copy(engine_path, "core/engine.py.bak_logger")
text = engine_path.read_text(encoding="utf-8", errors="ignore")

# 현재 삽입된 블록의 단순 문자열 교체 (인코딩 무관)
OLD = "logger.info('??OrderBookAnalyzer 珥덇린???꾨즺')"
NEW = "logger.info('✅ OrderBookAnalyzer 초기화 완료 (작동 중)')"

OLD2 = "logger.error(f'??OrderBookAnalyzer 珥덇린???ㅽ뙣: {_ob_err}')"
NEW2 = "logger.error(f'❌ OrderBookAnalyzer 초기화 실패: {_ob_err}')"

changed = 0
if OLD in text:
    text = text.replace(OLD, NEW, 1)
    changed += 1
    print("✅ 초기화 완료 로그 수정")
else:
    # 깨진 문자열 없이 이미 정상인 경우
    if "OrderBookAnalyzer 초기화 완료" in text:
        print("✔ 초기화 완료 로그 이미 정상")
        changed += 1
    else:
        # L194 직접 교체
        lines = text.splitlines()
        for i, ln in enumerate(lines):
            if "OrderBookAnalyzer" in ln and "logger.info" in ln and i in range(190, 200):
                lines[i] = "            logger.info('✅ OrderBookAnalyzer 초기화 완료 (작동 중)')"
                changed += 1
                print(f"✅ L{i+1} 직접 교체")
                break
        text = "\n".join(lines)

if OLD2 in text:
    text = text.replace(OLD2, NEW2, 1)
    print("✅ 실패 로그 수정")

engine_path.write_text(text, encoding="utf-8")
try:
    py_compile.compile(str(engine_path), doraise=True)
    print("✅ engine.py 문법 OK")
except py_compile.PyCompileError as e:
    print(f"❌ 문법 오류: {e}")
    shutil.copy("core/engine.py.bak_logger", engine_path)
    print("🔄 원본 복구")

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
        print("✅ orderbook_analyzer.py loguru 통일 완료")
    except py_compile.PyCompileError as e:
        print(f"❌ orderbook_analyzer.py 문법 오류: {e}")
else:
    print("✔ orderbook_analyzer.py 이미 loguru 사용 중")

print("\n✅ 수정 완료 → python start_paper.py")
