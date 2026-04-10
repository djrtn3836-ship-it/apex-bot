"""fix_orderbook_v2.py
 FIX-1 : engine.__init__ OrderBookAnalyzer  
 FIX-2 :   bypass (_in_pyramid )
 FIX-3 :   ExecutionRequest()     
          (f-string    –     statement )"""
import shutil, py_compile
from pathlib import Path

engine_path = Path("core/engine.py")
shutil.copy(engine_path, "core/engine.py.bak_ob2")
lines = engine_path.read_text(encoding="utf-8", errors="ignore").splitlines()

# ─────────────────────────────────────────────────────────────
# FIX-1 : OrderBookAnalyzer 초기화 – volume_spike 할당 다음 줄
# ─────────────────────────────────────────────────────────────
ob_block = [
    "",
    "        # ✅ OrderBook 분석기 초기화",
    "        try:",
    "            from data.processors.orderbook_analyzer import OrderBookAnalyzer",
    "            self.orderbook_analyzer = OrderBookAnalyzer()",
    "            logger.info(' OrderBookAnalyzer  ')",
    "        except Exception as _ob_err:",
    "            self.orderbook_analyzer = None",
    "            logger.warning(f' OrderBookAnalyzer  : {_ob_err}')",
]

fix1_done = False
out1 = []
for ln in lines:
    out1.append(ln)
    if (
        not fix1_done
        and "self.volume_spike" in ln
        and "=" in ln
        and "VolumeSpikeDetector" in ln  # 초기화 줄에만 매칭
    ):
        out1.extend(ob_block)
        fix1_done = True

if fix1_done:
    print(" FIX-1: OrderBookAnalyzer   ")
else:
    # fallback: VolumeSpikeDetector 없이 volume_spike = 로 재시도
    out1 = []
    for ln in lines:
        out1.append(ln)
        if (
            not fix1_done
            and "self.volume_spike" in ln
            and "=" in ln
            and "def " not in ln
        ):
            out1.extend(ob_block)
            fix1_done = True
    if fix1_done:
        print(" FIX-1 (fallback): OrderBookAnalyzer 초기화 삽입 완료")
    else:
        print(" FIX-1: volume_spike     –   ")

# ─────────────────────────────────────────────────────────────
# FIX-2 : 덤핑 bypass – _in_pyramid 조건 추가
# ─────────────────────────────────────────────────────────────
fix2_done = False
out2 = []
i = 0
while i < len(out1):
    ln = out1[i]
    # 정확히 "if is_dumping and not _is_bear_rev:" 줄만 교체
    stripped = ln.strip()
    if (
        not fix2_done
        and stripped.startswith("if is_dumping and not _is_bear_rev")
        and stripped.endswith(":")
        and "not _in_pyramid" not in stripped
    ):
        indent = len(ln) - len(ln.lstrip())
        sp = " " * indent
        out2.append(f"{sp}_in_pyramid = getattr(self, '_current_pyramid_market', None) == market")
        out2.append(f"{sp}if is_dumping and not _is_bear_rev and not _in_pyramid:")
        fix2_done = True
        i += 1
        continue
    out2.append(ln)
    i += 1

if fix2_done:
    print(" FIX-2:   bypass   ")
else:
    print(" FIX-2:    ")

# ─────────────────────────────────────────────────────────────
# FIX-3 : 피라미딩 마커 – ExecutionRequest( 생성 줄 바로 앞
#          조건: 해당 줄이 순수 statement (f-string 내부 아님)
#                이전 줄이 닫힌 상태 (따옴표 미완성 아님)
# ─────────────────────────────────────────────────────────────
fix3_done = False
out3 = []
i = 0
while i < len(out2):
    ln = out2[i]
    stripped = ln.strip()

    # ExecutionRequest( 로 시작하는 줄 탐색
    if (
        not fix3_done
        and "ExecutionRequest(" in stripped
        and stripped.startswith("req")       # req = ExecutionRequest( 형태
        and "=" in stripped
    ):
        # 앞 5줄에 '피라미딩' 관련 컨텍스트가 있는지 확인
        context = " ".join(out2[max(0, i-8):i])
        if "피라미딩" in context or "pyramid" in context.lower():
            indent = len(ln) - len(ln.lstrip())
            sp = " " * indent
            out3.append(f"{sp}self._current_pyramid_market = market  # 피라미딩 마커")
            fix3_done = True

    out3.append(ln)
    i += 1

if fix3_done:
    print(" FIX-3:    ")
else:
    # fallback: _check_position_exits 내 await self.executor.execute 직전
    out3_fb = []
    i = 0
    while i < len(out2):
        ln = out2[i]
        stripped = ln.strip()
        if (
            not fix3_done
            and "await self.executor.execute" in stripped
        ):
            context = " ".join(out2[max(0, i-10):i])
            if "피라미딩" in context or "pyramid" in context.lower():
                indent = len(ln) - len(ln.lstrip())
                sp = " " * indent
                out3_fb.append(f"{sp}self._current_pyramid_market = market  # 피라미딩 마커")
                fix3_done = True
        out3_fb.append(ln)
        i += 1
    if fix3_done:
        out3 = out3_fb
        print(" FIX-3 (fallback): 피라미딩 마커 삽입 완료")
    else:
        print(" FIX-3:     – FIX-1/2 ")

# ─────────────────────────────────────────────────────────────
# 저장 및 컴파일 검증
# ─────────────────────────────────────────────────────────────
final_text = "\n".join(out3)
engine_path.write_text(final_text, encoding="utf-8")

try:
    py_compile.compile(str(engine_path), doraise=True)
    print("\n engine.py  OK –   ")
    print("   : python start_paper.py")
except py_compile.PyCompileError as e:
    print(f"\n  : {e}")
    # 오류 주변 출력
    err_lines = final_text.splitlines()
    import re
    m = re.search(r"line (\d+)", str(e))
    if m:
        n = int(m.group(1))
        for j in range(max(0, n-3), min(len(err_lines), n+3)):
            print(f"  L{j+1}: {err_lines[j]}")
    shutil.copy("core/engine.py.bak_ob2", engine_path)
    print("   ")
