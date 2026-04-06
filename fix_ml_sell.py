import shutil, py_compile, sys

TARGET = "core/engine.py"
BACKUP = "core/engine.py.bak_ml_sell"
shutil.copy(TARGET, BACKUP)
print("✅ 백업 완료:", BACKUP)

with open(TARGET, "r", encoding="utf-8") as f:
    content = f.read()

OLD = '''            # 익절 조건: ML SELL 신뢰도 > 0.75, 수익 > 1 %
            if signal == "SELL" and confidence > 0.75 and pnl_pct > 1.0:
                logger.info(
                    f"🎯 ML 익절 신호 | {market} | 신뢰도={confidence:.2f} | 수익={pnl_pct:+.2f}%"
                )'''

NEW = '''            # 익절 조건: ML SELL 신뢰도 > 0.75, 수익 > 1%
            if signal == "SELL" and confidence > 0.75 and pnl_pct > 1.0:
                logger.info(
                    f"🎯 ML 익절 실행 | {market} | 신뢰도={confidence:.2f} | 수익={pnl_pct:+.2f}%"
                )
                await self._execute_sell(
                    market,
                    f"ML익절_{pnl_pct:.1f}%",
                    current_price,
                )
                return'''

if OLD in content:
    content = content.replace(OLD, NEW)
    print("✅ ML SELL 실행 연결 완료")
else:
    print("❌ 패턴 없음 - 수동 확인 필요")

with open(TARGET, "w", encoding="utf-8") as f:
    f.write(content)

try:
    py_compile.compile(TARGET, doraise=True)
    print("✅ 문법 검사 OK")
except py_compile.PyCompileError as e:
    print("❌ 문법 오류:", e)
    shutil.copy(BACKUP, TARGET)
    print("🔁 원본 복구 완료")
    sys.exit(1)

print("🎉 1번 완료!")
