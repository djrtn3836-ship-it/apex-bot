"""fix_unlimited_analysis.py
1.     (   )
2.   /  
3. max_positions"""
import shutil, py_compile, re
from pathlib import Path

engine_path = Path("core/engine.py")
shutil.copy(engine_path, "core/engine.py.bak_unlimited")
text = engine_path.read_text(encoding="utf-8", errors="ignore")

# ── FIX-1: _cycle에서 포지션 수 조건 제거 + 전체 코인 분석 ───────────────
# 현재:
#   if self.portfolio.position_count < self.settings.trading.max_positions:
#       analysis_tasks = [...]
#       await asyncio.gather(...)
# 수정:
#   항상 전체 코인 분석 실행

lines = text.splitlines()
fix1_done = False
out = []
i = 0

while i < len(lines):
    ln = lines[i]

    # "포지션 수 < max_positions" 조건 블록 탐색
    if (
        not fix1_done
        and "position_count" in ln
        and "max_positions" in ln
        and "if " in ln
    ):
        indent = " " * (len(ln) - len(ln.lstrip()))
        # 조건 블록 끝까지 스킵 후 새 코드로 교체
        block_lines = []
        i += 1
        while i < len(lines):
            cur = lines[i]
            cur_indent = len(cur) - len(cur.lstrip()) if cur.strip() else 999
            # 조건 블록 내부 수집
            if cur.strip() == "" or cur_indent > len(indent):
                block_lines.append(cur)
                i += 1
            else:
                break

        # 새 분석 블록 (조건 없이 항상 실행)
        sp = indent  # 기존 if와 같은 들여쓰기
        new_block = [
            f"{sp}# ── 전체 코인 분석 (포지션 수 무관하게 항상 실행) ──────────────",
            f"{sp}# 신규 진입 후보: 포지션 없는 코인",
            f"{sp}new_entry_markets = [",
            f"{sp}    m for m in markets",
            f"{sp}    if not self.portfolio.is_position_open(m)",
            f"{sp}]",
            f"{sp}# 기존 포지션 코인: ML 재평가 + 익절/추가매수 탐색",
            f"{sp}existing_markets = [",
            f"{sp}    m for m in markets",
            f"{sp}    if self.portfolio.is_position_open(m)",
            f"{sp}]",
            f"{sp}# 신규 진입은 잔고와 포지션 수로만 제한 (분석은 항상 실행)",
            f"{sp}can_enter_new = (",
            f"{sp}    self.portfolio.position_count < self.settings.trading.max_positions",
            f"{sp}    and krw >= self.settings.trading.min_order_amount",
            f"{sp})",
            f"{sp}entry_tasks = [",
            f"{sp}    self._analyze_market(m)",
            f"{sp}    for m in new_entry_markets",
            f"{sp}] if can_enter_new else []",
            f"{sp}exist_tasks = [",
            f"{sp}    self._analyze_existing_position(m)",
            f"{sp}    for m in existing_markets",
            f"{sp}]",
            f"{sp}await asyncio.gather(*(entry_tasks + exist_tasks), return_exceptions=True)",
        ]
        out.extend(new_block)
        fix1_done = True
        continue

    out.append(ln)
    i += 1

if fix1_done:
    text = "\n".join(out)
    print(" FIX-1:    (  ) 적용 완료")
else:
    print(" FIX-1:     –    ")

# ── FIX-2: _analyze_existing_position 없으면 추가 ────────────────────────
if "_analyze_existing_position" not in text:
    NEW_METHOD = '''
    async def _analyze_existing_position(self, market: str):
        """ML  – /"""
        try:
            df = self.cache_manager.get_candles(market, "1h")
            if df is None or len(df) < 10:
                return
            ml_pred = await self._get_ml_prediction(market, df)
            if not ml_pred:
                return
            signal  = ml_pred.get("signal", "HOLD")
            conf    = ml_pred.get("confidence", 0.0)
            pos     = self.portfolio.get_position(market)
            if pos is None:
                return
            entry   = getattr(pos, "entry_price", 0)
            current = self._market_prices.get(market, entry)
            pnl_pct = (current - entry) / entry * 100 if entry > 0 else 0
            logger.debug(
                f"   | {market} | "
                f"ML={signal}({conf:.2f}) | PnL={pnl_pct:+.2f}%"
            )
            if signal == "SELL" and conf > 0.75 and pnl_pct > 1.0:
                logger.info(
                    f" ML   | {market} | "
                    f"={conf:.2f} | ={pnl_pct:+.2f}%"
                )
        except Exception as e:
            logger.debug(f"   ({market}): {e}")

'''
    lines2 = text.splitlines()
    for idx, ln in enumerate(lines2):
        if "async def _analyze_market" in ln:
            lines2 = lines2[:idx] + NEW_METHOD.splitlines() + lines2[idx:]
            text = "\n".join(lines2)
            print(" FIX-2: _analyze_existing_position  ")
            break
else:
    print(" FIX-2: _analyze_existing_position  ")

# ── FIX-3: 1시간 텔레그램 스케줄 ────────────────────────────────────────
if "hourly_telegram_summary" not in text:
    lines3 = text.splitlines()
    for idx, ln in enumerate(lines3):
        if "스케줄러 등록 완료" in ln and "logger" in ln:
            indent = " " * (len(ln) - len(ln.lstrip()))
            block = [
                f"{indent}# 1시간 텔레그램 자동 현황 요약",
                f"{indent}scheduler.add_job(",
                f"{indent}    self.telegram.send_hourly_summary,",
                f"{indent}    'interval', hours=1,",
                f"{indent}    id='hourly_telegram_summary',",
                f"{indent}    name='1시간 텔레그램 요약',",
                f"{indent}    misfire_grace_time=60",
                f"{indent})",
            ]
            lines3 = lines3[:idx] + block + lines3[idx:]
            text = "\n".join(lines3)
            print(" FIX-3: 1    ")
            break
else:
    print(" FIX-3:   ")

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
        for j in range(max(0, n-4), min(len(err_lines), n+5)):
            print(f"  L{j+1}: {err_lines[j]}")
    shutil.copy("core/engine.py.bak_unlimited", engine_path)
    print("   ")
