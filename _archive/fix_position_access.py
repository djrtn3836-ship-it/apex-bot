# fix_position_access.py
"""FIX-1: _analyze_existing_position  pos.get() → pos.avg_price / pos.entry_price  
FIX-2: candles    
FIX-3: Position"""
import shutil, py_compile, re
from pathlib import Path

ENGINE = Path("core/engine.py")
shutil.copy(ENGINE, ENGINE.with_suffix(".py.bak_pos"))
print("  ")

text = ENGINE.read_text(encoding="utf-8", errors="ignore")

# ── FIX-1: pos.get() → 안전한 속성 접근으로 교체 ─────────────────────────────
OLD_ENTRY = '            entry_price   = pos.get("avg_price", pos.get("entry_price", 0))'
NEW_ENTRY = '''\
            # Position 객체 속성 안전 접근 (dataclass or dict 모두 지원)
            if hasattr(pos, 'avg_price'):
                entry_price = getattr(pos, 'avg_price', 0) or getattr(pos, 'entry_price', 0)
            elif hasattr(pos, 'entry_price'):
                entry_price = getattr(pos, 'entry_price', 0)
            elif isinstance(pos, dict):
                entry_price = pos.get("avg_price", pos.get("entry_price", 0))
            else:
                entry_price = 0'''

if OLD_ENTRY in text:
    text = text.replace(OLD_ENTRY, NEW_ENTRY, 1)
    print(" FIX-1: entry_price    ")
else:
    # 유사 패턴 탐색
    pattern = r'entry_price\s*=\s*pos\.get\("avg_price"'
    if re.search(pattern, text):
        text = re.sub(
            r'(\s+)entry_price\s*=\s*pos\.get\("avg_price",\s*pos\.get\("entry_price",\s*0\)\)',
            r'''\1# Position 객체 속성 안전 접근
\1if hasattr(pos, 'avg_price'):
\1    entry_price = getattr(pos, 'avg_price', 0) or getattr(pos, 'entry_price', 0)
\1elif hasattr(pos, 'entry_price'):
\1    entry_price = getattr(pos, 'entry_price', 0)
\1elif isinstance(pos, dict):
\1    entry_price = pos.get("avg_price", pos.get("entry_price", 0))
\1else:
\1    entry_price = 0''',
            text, count=1
        )
        print(" FIX-1: fallback entry_price  ")
    else:
        print("  FIX-1:   –   ")

# ── FIX-2: candles 길이 체크 강화 ────────────────────────────────────────────
OLD_LEN_CHECK = "            if candles is None or len(candles) < 20:\n                return"
NEW_LEN_CHECK = """\
            # candles    (DataFrame / list / None  )
            try:
                _candle_len = len(candles) if candles is not None else 0
            except Exception:
                _candle_len = 0
            if _candle_len < 20:
                return"""

if OLD_LEN_CHECK in text:
    text = text.replace(OLD_LEN_CHECK, NEW_LEN_CHECK, 1)
    print(" FIX-2: candles    ")
else:
    print("  FIX-2:   – ")

# ── FIX-3: 디버그 로그 강화 (오류 시 traceback 출력) ─────────────────────────
OLD_EXCEPT = "        except Exception as e:\n            logger.debug(f\"?ъ????ы ? ({market}): {e}\")"
NEW_EXCEPT = """\
        except Exception as e:
            import traceback
            logger.debug(f"   ({market}): {e} | {traceback.format_exc().splitlines()[-1]}")"""

# 인코딩 문제로 직접 매칭 어려우므로 정규식 사용
text = re.sub(
    r'        except Exception as e:\s*\n\s+logger\.debug\(f"[^"]*\(\{market\}\)[^"]*"\)',
    NEW_EXCEPT,
    text,
    count=1
)
print(" FIX-3:    ")

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
    shutil.copy(ENGINE.with_suffix(".py.bak_pos"), ENGINE)
    print(" engine.py   ")
    exit(1)
