# fix_add_method.py
"""
_analyze_existing_position 메서드를 TradingEngine 클래스 내부에 안전하게 삽입
"""
import shutil, py_compile
from pathlib import Path

ENGINE = Path("core/engine.py")
BACKUP = Path("core/engine.py.bak_method")

# ── 백업 ──────────────────────────────────────────────────────────────────────
shutil.copy(ENGINE, BACKUP)
print(f"📦 백업: {BACKUP}")

lines = ENGINE.read_text(encoding="utf-8", errors="ignore").splitlines()

# ── 삽입할 메서드 (4-space indent = class 내부) ───────────────────────────────
NEW_METHOD = '''
    async def _analyze_existing_position(self, market: str) -> None:
        """기존 포지션 ML 재평가 – 익절/손절 시그널 감지"""
        try:
            pos = self.portfolio.get_position(market)
            if pos is None:
                return

            # 캔들 데이터 확보
            candles = self.cache_manager.get_ohlcv(market, "1h")
            if candles is None or len(candles) < 20:
                return

            # ML 예측
            ml_result = await self._get_ml_prediction(market, candles)
            if ml_result is None:
                return

            signal     = ml_result.get("signal", "HOLD")
            confidence = ml_result.get("confidence", 0.0)

            # 현재 PnL 계산
            entry_price   = pos.get("avg_price", pos.get("entry_price", 0))
            current_price = self._market_prices.get(market, 0)
            pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0

            logger.debug(
                f"📊 포지션 재평가 | {market} | "
                f"ML={signal}({confidence:.2f}) | PnL={pnl_pct:+.2f}%"
            )

            # 익절 조건: ML SELL 신뢰도 > 0.75, 수익 > 1 %
            if signal == "SELL" and confidence > 0.75 and pnl_pct > 1.0:
                logger.info(
                    f"🎯 ML 익절 신호 | {market} | 신뢰도={confidence:.2f} | 수익={pnl_pct:+.2f}%"
                )

        except Exception as e:
            logger.debug(f"포지션 재평가 오류 ({market}): {e}")
'''

# ── 삽입 위치: _analyze_market 정의 바로 앞 ──────────────────────────────────
insert_idx = None
for i, ln in enumerate(lines):
    if "async def _analyze_market" in ln and ln.startswith("    "):
        insert_idx = i
        break

if insert_idx is None:
    # fallback: _main_loop 바로 앞
    for i, ln in enumerate(lines):
        if "async def _main_loop" in ln and ln.startswith("    "):
            insert_idx = i
            break

if insert_idx is None:
    print("❌ 삽입 위치를 찾지 못했습니다. 수동 확인 필요.")
    exit(1)

# 이미 존재하면 건너뜀
if any("async def _analyze_existing_position" in ln for ln in lines):
    print("⚠️  _analyze_existing_position 이미 존재 – 삽입 건너뜀")
else:
    for i, ln in enumerate(NEW_METHOD.splitlines()):
        lines.insert(insert_idx + i, ln)
    print(f"✅ _analyze_existing_position 삽입 완료 (L{insert_idx})")

# ── 저장 ──────────────────────────────────────────────────────────────────────
ENGINE.write_text("\n".join(lines), encoding="utf-8")

# ── 문법 검사 ─────────────────────────────────────────────────────────────────
try:
    py_compile.compile(str(ENGINE), doraise=True)
    print("✅ engine.py 문법 OK")
    print("   다음: python start_paper.py")
except py_compile.PyCompileError as e:
    print(f"❌ 문법 오류: {e}")
    shutil.copy(BACKUP, ENGINE)
    print("🔄 원본 복구 완료")
