# patch_sell_record.py
# engine_sell.py 의 record_trade_result 호출을 profit_rate 포함 버전으로 교체

import os, ast, shutil
from datetime import datetime

_TS  = datetime.now().strftime("%Y%m%d_%H%M%S")
_BAK = os.path.join("archive", f"patch_sell_{_TS}")
os.makedirs(_BAK, exist_ok=True)
print(f"\n📁 백업 경로: {_BAK}\n")

SELL_PATH = os.path.join("core", "engine_sell.py")

if not os.path.isfile(SELL_PATH):
    print(f"❌ 파일 없음: {SELL_PATH}")
    exit(1)

with open(SELL_PATH, "r", encoding="utf-8") as f:
    src = f.read()

# ── 패턴 교체 ────────────────────────────────────────────────────────────────
OLD = "            self.risk_manager.record_trade_result(profit_rate > 0)"

NEW = (
    "            # [BUG-REAL-1-C FIX] profit_rate(%) 를 소수로 변환해 함께 전달\n"
    "            self.risk_manager.record_trade_result(\n"
    "                is_win=profit_rate > 0,\n"
    "                profit_rate=profit_rate / 100.0,  # % → 소수 변환\n"
    "            )"
)

print("=" * 60)
print("[PATCH] core/engine_sell.py  record_trade_result 호출부 수정")
print("=" * 60)

if OLD not in src:
    # 인덴트 변형 대비 폴백 탐색
    import re
    pattern = re.compile(
        r'( *self\.risk_manager\.record_trade_result\(profit_rate\s*>\s*0\))'
    )
    m = pattern.search(src)
    if m:
        indent = len(m.group(1)) - len(m.group(1).lstrip())
        pad = " " * indent
        replacement = (
            f"{pad}# [BUG-REAL-1-C FIX] profit_rate(%) 를 소수로 변환해 함께 전달\n"
            f"{pad}self.risk_manager.record_trade_result(\n"
            f"{pad}    is_win=profit_rate > 0,\n"
            f"{pad}    profit_rate=profit_rate / 100.0,  # % → 소수 변환\n"
            f"{pad})"
        )
        new_src = src[:m.start()] + replacement + src[m.end():]
        print("  ℹ️  폴백 정규식으로 패턴 탐색 성공")
    else:
        print("  ⚠️  패턴을 찾을 수 없습니다 — 이미 수정됐거나 코드 구조가 다릅니다.")
        print("       아래 내용을 직접 확인하세요:")
        print("       grep -n 'record_trade_result' core/engine_sell.py")
        exit(0)
else:
    new_src = src.replace(OLD, NEW, 1)
    print("  ✅ 정확한 패턴 매칭 성공")

# ── 문법 검사 ────────────────────────────────────────────────────────────────
try:
    ast.parse(new_src)
except SyntaxError as e:
    print(f"  ❌ 문법 오류 — 원본 유지: {e}")
    exit(1)

# ── 백업 및 저장 ─────────────────────────────────────────────────────────────
shutil.copy2(SELL_PATH, os.path.join(_BAK, "engine_sell.py.bak"))
with open(SELL_PATH, "w", encoding="utf-8") as f:
    f.write(new_src)

# ── 재검증 ───────────────────────────────────────────────────────────────────
with open(SELL_PATH, "r", encoding="utf-8") as f:
    verify = f.read()

ok = "profit_rate / 100.0" in verify and "is_win=profit_rate > 0" in verify

print()
print("=" * 60)
print("🔍 재검증")
print("=" * 60)
print(f"  {'✅' if ok else '❌'}  record_trade_result profit_rate 전달: {'적용됨' if ok else '실패'}")

print()
if ok:
    print("✅ 모든 BUG-REAL-1-C 수정 완료!")
    print()
    print("┌─────────────────────────────────────────────────────┐")
    print("│            🎉 전체 버그 수정 완료 요약               │")
    print("├─────────────────────────────────────────────────────┤")
    print("│  BUG-REAL-1  risk_manager.py  동적 Kelly 계산  ✅   │")
    print("│  BUG-REAL-2  engine_cycle.py  _ml_df 초기화    ✅   │")
    print("│  BUG-REAL-3  engine_ml.py     dashboard 임포트 ✅   │")
    print("│  BUG-REAL-4  order_block_v2   open_arr 버그    ✅   │")
    print("│  BUG-REAL-5  signal_combiner  가중치 통일      ✅   │")
    print("│  QUALITY-2   engine_buy.py    안정성 개선      ✅   │")
    print("└─────────────────────────────────────────────────────┘")
    print()
    print("📋 다음 단계:")
    print("   python main.py --mode paper")
    print("   → 24시간 후 로그 확인:")
    print('     Select-String -Path logs\\*.log -Pattern "kelly|avg_win|avg_loss"')
else:
    print("❌ 수정 실패 — 수동 수정 가이드:")
    print()
    print("   core/engine_sell.py 에서 아래 줄을 찾아:")
    print("   self.risk_manager.record_trade_result(profit_rate > 0)")
    print()
    print("   다음으로 교체하세요:")
    print("   self.risk_manager.record_trade_result(")
    print("       is_win=profit_rate > 0,")
    print("       profit_rate=profit_rate / 100.0,")
    print("   )")
