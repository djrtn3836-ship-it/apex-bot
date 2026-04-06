import shutil, py_compile, sys

TARGET = "core/engine.py"
BACKUP = "core/engine.py.bak_restore_fix"
shutil.copy(TARGET, BACKUP)
print("✅ 백업 완료:", BACKUP)

with open(TARGET, "r", encoding="utf-8") as f:
    content = f.read()

OLD = '''                self.portfolio.open_position(
                    market      = mkt,
                    entry_price = row["price"],
                    volume      = row["volume"],
                    amount_krw  = row["amount_krw"],
                    strategy    = row["strategy"] or "unknown",
                    stop_loss   = row["price"] * 0.97,
                    take_profit = row["price"] * 1.05,
                )'''

NEW = '''                # None 방어: DB 값이 None일 경우 기본값 적용
                _price      = float(row["price"]      or 0)
                _volume     = float(row["volume"]     or 0)
                _amount_krw = float(row["amount_krw"] or 0)
                if _price <= 0 or _volume <= 0:
                    logger.warning(f"포지션 복원 스킵 ({mkt}): 가격/수량 없음")
                    continue
                self.portfolio.open_position(
                    market      = mkt,
                    entry_price = _price,
                    volume      = _volume,
                    amount_krw  = _amount_krw,
                    strategy    = row["strategy"] or "unknown",
                    stop_loss   = _price * 0.97,
                    take_profit = _price * 1.05,
                )'''

if OLD in content:
    content = content.replace(OLD, NEW)
    print("✅ 포지션 복원 None 방어 추가 완료")
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

print("🎉 포지션 복원 버그 수정 완료!")
