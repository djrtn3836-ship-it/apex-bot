import shutil, py_compile, sys

TARGET = "core/engine.py"
BACKUP = "core/engine.py.bak_scanner_call"
shutil.copy(TARGET, BACKUP)
print("✅ 백업 완료:", BACKUP)

with open(TARGET, "r", encoding="utf-8") as f:
    content = f.read()

OLD = '''        # ✅ 트레일링 스탑 + 부분 청산 체크
        await self._check_position_exits()'''

NEW = '''        # ✅ 전체 마켓 스캐너 (급등 코인 동적 포착)
        try:
            new_surge_markets = await self._market_scanner()
            if new_surge_markets:
                for _sm in new_surge_markets:
                    if _sm not in self.markets:
                        self.markets = list(self.markets) + [_sm]
                        logger.info(f"🔥 급등 코인 감시 추가: {_sm}")
        except Exception as _se:
            logger.debug(f"마켓 스캐너 오류: {_se}")

        # ✅ 트레일링 스탑 + 부분 청산 체크
        await self._check_position_exits()'''

if OLD in content:
    content = content.replace(OLD, NEW)
    print("✅ _market_scanner() 호출 추가 완료")
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

print("🎉 2번 완료!")
