import shutil, py_compile, sys, re

TARGET = "core/engine.py"
BACKUP = "core/engine.py.bak_remove_profiles"
shutil.copy(TARGET, BACKUP)
print("✅ 백업 완료:", BACKUP)

with open(TARGET, "r", encoding="utf-8") as f:
    content = f.read()

# _COIN_ATR_PROFILES 딕셔너리 블록 제거 (engine.__init__ 안의 것만)
OLD = '''        # ✅ 코인별 ATR 프로필 (가격대 기반 자동 선택)
        self._COIN_ATR_PROFILES = {
            "DEFAULT": {"atr_low": 0.018, "atr_high": 0.048},
            "PENNY":   {"atr_low": 0.020, "atr_high": 0.060},
        }'''

if OLD in content:
    content = content.replace(OLD, "")
    print("✅ engine.__init__ _COIN_ATR_PROFILES 제거 완료")
else:
    # 더 큰 블록으로 시도
    pattern = r'        # ✅ 코인별 ATR 프로필.*?}\s*}\n'
    new_content = re.sub(pattern, '', content, flags=re.DOTALL)
    if new_content != content:
        content = new_content
        print("✅ _COIN_ATR_PROFILES 제거 완료 (regex)")
    else:
        print("ℹ️  _COIN_ATR_PROFILES 없음 - 건너뜀")

# _run_backtest_v2 안의 self._COIN_ATR_PROFILES 참조도 제거
OLD2 = '''            profile = self._COIN_ATR_PROFILES.get(
                market, self._COIN_ATR_PROFILES["DEFAULT"]
            )'''
NEW2 = '''            # 가격 기반 동적 프로필 (atr_stop.py의 _get_profile_by_price 사용)
            from risk.stop_loss.atr_stop import _get_profile_by_price
            _entry_est = float(df["close"].iloc[-1]) if len(df) > 0 else 1000
            _p = _get_profile_by_price(_entry_est)
            profile = {"atr_low": _p["min_sl"], "atr_high": _p["max_sl"]}'''

if OLD2 in content:
    content = content.replace(OLD2, NEW2)
    print("✅ _run_backtest_v2 프로필 참조 → 동적 프로필로 교체 완료")
else:
    print("ℹ️  _run_backtest_v2 프로필 참조 없음 - 건너뜀")

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

print("🎉 3번 완료!")
