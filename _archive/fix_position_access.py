# fix_position_access.py
"""
FIX-1: _analyze_existing_position 내부 pos.get() → pos.avg_price / pos.entry_price 속성 접근
FIX-2: candles 길이 체크 안전하게 수정
FIX-3: Position 클래스 속성 확인
"""
import shutil, py_compile, re
from pathlib import Path

ENGINE = Path("core/engine.py")
shutil.copy(ENGINE, ENGINE.with_suffix(".py.bak_pos"))
print("📦 백업 완료")

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
    print("✅ FIX-1: entry_price 접근 방식 수정 완료")
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
        print("✅ FIX-1: fallback entry_price 수정 완료")
    else:
        print("⚠️  FIX-1: 패턴 없음 – 수동 확인 필요")

# ── FIX-2: candles 길이 체크 강화 ────────────────────────────────────────────
OLD_LEN_CHECK = "            if candles is None or len(candles) < 20:\n                return"
NEW_LEN_CHECK = """\
            # candles 길이 안전 체크 (DataFrame / list / None 모두 처리)
            try:
                _candle_len = len(candles) if candles is not None else 0
            except Exception:
                _candle_len = 0
            if _candle_len < 20:
                return"""

if OLD_LEN_CHECK in text:
    text = text.replace(OLD_LEN_CHECK, NEW_LEN_CHECK, 1)
    print("✅ FIX-2: candles 길이 체크 강화 완료")
else:
    print("⚠️  FIX-2: 패턴 없음 – 건너뜀")

# ── FIX-3: 디버그 로그 강화 (오류 시 traceback 출력) ─────────────────────────
OLD_EXCEPT = "        except Exception as e:\n            logger.debug(f\"?ъ????ы룊媛 ?ㅻ쪟 ({market}): {e}\")"
NEW_EXCEPT = """\
        except Exception as e:
            import traceback
            logger.debug(f"포지션 재평가 오류 ({market}): {e} | {traceback.format_exc().splitlines()[-1]}")"""

# 인코딩 문제로 직접 매칭 어려우므로 정규식 사용
text = re.sub(
    r'        except Exception as e:\s*\n\s+logger\.debug\(f"[^"]*\(\{market\}\)[^"]*"\)',
    NEW_EXCEPT,
    text,
    count=1
)
print("✅ FIX-3: 오류 로그 강화 완료")

ENGINE.write_text(text, encoding="utf-8")

# ── 문법 검사 ─────────────────────────────────────────────────────────────────
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
    shutil.copy(ENGINE.with_suffix(".py.bak_pos"), ENGINE)
    print("🔄 engine.py 원본 복구 완료")
    exit(1)
