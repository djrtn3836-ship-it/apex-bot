"""fix_main_loop.py
  3:
1.   _analyze_market  (ML  +  )
2. 1     
3."""
import shutil, py_compile, re
from pathlib import Path

engine_path = Path("core/engine.py")
shutil.copy(engine_path, "core/engine.py.bak_loop")
text = engine_path.read_text(encoding="utf-8", errors="ignore")
lines = text.splitlines()

# ── FIX-1: 기존 포지션도 분석 대상 포함 ─────────────────────────────────
# 현재:
#   analysis_tasks = [
#       self._analyze_market(market)
#       for market in markets
#       if not self.portfolio.is_position_open(market)   ← 이 조건 제거
#   ]
OLD_ANALYSIS = (
    "            analysis_tasks = [\n"
    "                self._analyze_market(market)\n"
    "                for market in markets\n"
    "                if not self.portfolio.is_position_open(market)\n"
    "            ]\n"
    "            await asyncio.gather(*analysis_tasks, return_exceptions=True)"
)
NEW_ANALYSIS = (
    "            # 신규 진입 후보 (포지션 없는 코인)\n"
    "            new_entry_tasks = [\n"
    "                self._analyze_market(market)\n"
    "                for market in markets\n"
    "                if not self.portfolio.is_position_open(market)\n"
    "            ]\n"
    "            # 기존 포지션 코인도 ML 재평가 (익절/추가매수 기회 탐색)\n"
    "            existing_tasks = [\n"
    "                self._analyze_existing_position(market)\n"
    "                for market in markets\n"
    "                if self.portfolio.is_position_open(market)\n"
    "            ]\n"
    "            all_tasks = new_entry_tasks + existing_tasks\n"
    "            await asyncio.gather(*all_tasks, return_exceptions=True)"
)

if OLD_ANALYSIS in text:
    text = text.replace(OLD_ANALYSIS, NEW_ANALYSIS, 1)
    print(" FIX-1:     ")
else:
    # 인코딩 깨진 경우 라인 번호 기반 패치
    fixed = False
    out = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if "analysis_tasks" in ln and "self._analyze_market" in lines[i+1] if i+1 < len(lines) else False:
            indent = " " * (len(ln) - len(ln.lstrip()))
            out.extend([
                f"{indent}# 신규 진입 후보 (포지션 없는 코인)",
                f"{indent}new_entry_tasks = [",
                f"{indent}    self._analyze_market(market)",
                f"{indent}    for market in markets",
                f"{indent}    if not self.portfolio.is_position_open(market)",
                f"{indent}]",
                f"{indent}# 기존 포지션 코인 ML 재평가",
                f"{indent}existing_tasks = [",
                f"{indent}    self._analyze_existing_position(market)",
                f"{indent}    for market in markets",
                f"{indent}    if self.portfolio.is_position_open(market)",
                f"{indent}]",
                f"{indent}all_tasks = new_entry_tasks + existing_tasks",
            ])
            # 기존 gather 라인까지 스킵
            while i < len(lines) and "asyncio.gather" not in lines[i]:
                i += 1
            out.append(f"{indent}await asyncio.gather(*all_tasks, return_exceptions=True)")
            fixed = True
            i += 1
            continue
        out.append(ln)
        i += 1
    if fixed:
        text = "\n".join(out)
        lines = text.splitlines()
        print(" FIX-1 (fallback): 기존 포지션 분석 추가 완료")
    else:
        print(" FIX-1:   ")

# ── FIX-2: _analyze_existing_position 메서드 추가 ────────────────────────
# _analyze_market 함수 정의 바로 앞에 삽입
NEW_METHOD = '''
    async def _analyze_existing_position(self, market: str):
        """ML  – /"""
        try:
            df = self.cache_manager.get_candles(market, "1h")
            if df is None or len(df) < 10:
                return
            # ML 예측
            ml_pred = await self._get_ml_prediction(market, df)
            if not ml_pred:
                return
            signal   = ml_pred.get("signal", "HOLD")
            conf     = ml_pred.get("confidence", 0.0)
            pos      = self.portfolio.get_position(market)
            if pos is None:
                return
            entry    = getattr(pos, "entry_price", 0)
            current  = self._market_prices.get(market, entry)
            pnl_pct  = (current - entry) / entry * 100 if entry > 0 else 0
            logger.debug(
                f"   | {market} | "
                f"ML={signal}({conf:.2f}) | PnL={pnl_pct:+.2f}%"
            )
            # ML이 SELL 신호 + 신뢰도 높으면 익절 검토
            if signal == "SELL" and conf > 0.75 and pnl_pct > 1.0:
                logger.info(
                    f" ML   | {market} | "
                    f"={conf:.2f} | ={pnl_pct:+.2f}%"
                )
        except Exception as e:
            logger.debug(f"   ({market}): {e}")

'''

# _analyze_market 정의 바로 앞에 삽입
insert_done = False
out_lines = text.splitlines()
for idx, ln in enumerate(out_lines):
    if "async def _analyze_market" in ln and not insert_done:
        out_lines = out_lines[:idx] + NEW_METHOD.splitlines() + out_lines[idx:]
        insert_done = True
        break

if insert_done:
    text = "\n".join(out_lines)
    print(" FIX-2: _analyze_existing_position   ")
else:
    print(" FIX-2: _analyze_market   ")

# ── FIX-3: 1시간 텔레그램 자동 요약 스케줄 등록 ─────────────────────────
SCHEDULE_MARKER = "✅ 스케줄러 등록 완료"
HOURLY_JOB = (
    "            # 1시간 텔레그램 자동 현황 요약\n"
    "            scheduler.add_job(\n"
    "                self.telegram.send_hourly_summary,\n"
    "                'interval',\n"
    "                hours=1,\n"
    "                id='hourly_telegram_summary',\n"
    "                name='1시간 텔레그램 요약'\n"
    "            )\n"
)

out2 = []
fix3_done = False
for ln in text.splitlines():
    if not fix3_done and SCHEDULE_MARKER in ln:
        out2.append(HOURLY_JOB)
        fix3_done = True
    out2.append(ln)

if fix3_done:
    text = "\n".join(out2)
    print(" FIX-3: 1     ")
else:
    print(" FIX-3:    ")

# ── 저장 및 컴파일 ───────────────────────────────────────────────────────
engine_path.write_text(text, encoding="utf-8")
try:
    py_compile.compile(str(engine_path), doraise=True)
    print("\n engine.py  OK –   ")
    print("   : python start_paper.py")
except py_compile.PyCompileError as e:
    print(f"\n  : {e}")
    m = re.search(r"line (\d+)", str(e))
    if m:
        n = int(m.group(1))
        err_lines = text.splitlines()
        for j in range(max(0, n-4), min(len(err_lines), n+4)):
            print(f"  L{j+1}: {err_lines[j]}")
    shutil.copy("core/engine.py.bak_loop", engine_path)
    print("   ")
