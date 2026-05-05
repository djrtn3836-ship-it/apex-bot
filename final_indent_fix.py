# final_indent_fix.py
# engine_sell.py 의 들여쓰기 버그 수정
# live_guard 콜백 + log_trade + telegram.notify_sell 이
# except 블록 안에 잘못 포함된 문제 교정

import os, ast, shutil
from datetime import datetime

_TS  = datetime.now().strftime("%Y%m%d_%H%M%S")
_BAK = os.path.join("archive", f"indent_fix_{_TS}")
os.makedirs(_BAK, exist_ok=True)
print(f"\n📁 백업 경로: {_BAK}\n")

SELL_PATH = os.path.join("core", "engine_sell.py")

if not os.path.isfile(SELL_PATH):
    print(f"❌ 파일 없음: {SELL_PATH}")
    exit(1)

with open(SELL_PATH, "r", encoding="utf-8") as f:
    src = f.read()

# ── 패턴: except 블록 안에 잘못 들여쓰기된 코드 교정 ─────────────────────────
# 현재 (잘못된 상태): live_guard/log_trade/telegram 이 except 안에 있음
OLD_INDENT = '''        except Exception as _scd_e:
            logger.debug(f"[STRAT-CD] 처리 중 오류(무시): {_scd_e}")
            # ── 전략별 개별 쿨다운 끝 ────────────────────────────────────
            # [LiveGuard] 매도 결과 콜백 — 연속 손실 추적
            try:
                if hasattr(self, 'live_guard') and self.live_guard is not None:
                    await self.live_guard.on_trade_result(profit_rate / 100.0, market)  # FIX: % -> 소수
                    # [LiveGuard] 조건C: 일일 손실 누적 업데이트
                    if profit_rate < 0:
                        _prev = getattr(self.live_guard, '_today_loss_pct', 0.0)
                        self.live_guard._today_loss_pct = _prev + (profit_rate / 100.0)
            except Exception as _lg_e:
                logger.debug(f'[LiveGuard] on_trade_result 호출 실패: {_lg_e}')
            log_trade(
            "SELL", market, result.executed_price,
            proceeds, reason, profit_rate
            )
            await self.telegram.notify_sell(
            market, result.executed_price, result.executed_volume,
            profit_rate, reason  # [FIX] 소수->% 변환
            )'''

NEW_INDENT = '''        except Exception as _scd_e:
            logger.debug(f"[STRAT-CD] 처리 중 오류(무시): {_scd_e}")
        # ── 전략별 개별 쿨다운 끝 ────────────────────────────────────

        # [LiveGuard] 매도 결과 콜백 — 연속 손실 추적
        try:
            if hasattr(self, 'live_guard') and self.live_guard is not None:
                await self.live_guard.on_trade_result(profit_rate / 100.0, market)
                if profit_rate < 0:
                    _prev = getattr(self.live_guard, '_today_loss_pct', 0.0)
                    self.live_guard._today_loss_pct = _prev + (profit_rate / 100.0)
        except Exception as _lg_e:
            logger.debug(f'[LiveGuard] on_trade_result 호출 실패: {_lg_e}')

        log_trade(
            "SELL", market, result.executed_price,
            proceeds, reason, profit_rate
        )
        await self.telegram.notify_sell(
            market, result.executed_price, result.executed_volume,
            profit_rate, reason
        )'''

print("=" * 60)
print("[INDENT-FIX] core/engine_sell.py 들여쓰기 버그 수정")
print("=" * 60)

if OLD_INDENT not in src:
    print("  ℹ️  패턴 없음 — 이미 수정됐거나 코드 구조가 다릅니다.")
    print("  → 아래 명령으로 직접 확인하세요:")
    print("    Select-String -Path core\\engine_sell.py -Pattern 'log_trade|notify_sell'")
    exit(0)

new_src = src.replace(OLD_INDENT, NEW_INDENT, 1)

try:
    ast.parse(new_src)
except SyntaxError as e:
    print(f"  ❌ 문법 오류: {e}")
    exit(1)

shutil.copy2(SELL_PATH, os.path.join(_BAK, "engine_sell.py.bak"))
with open(SELL_PATH, "w", encoding="utf-8") as f:
    f.write(new_src)

with open(SELL_PATH, "r", encoding="utf-8") as f:
    verify = f.read()

ok = (
    '        log_trade(\n            "SELL"' in verify
    and "        await self.telegram.notify_sell(" in verify
)

print(f"  {'✅' if ok else '❌'}  들여쓰기 교정: {'완료' if ok else '실패'}")

if ok:
    print()
    print("=" * 60)
    print("🎉 APEX BOT 전체 버그 수정 최종 완료!")
    print("=" * 60)
    print()
    print("  수정된 파일 목록:")
    print("  ✅  risk/risk_manager.py        동적 Kelly + profit_rate 저장")
    print("  ✅  core/engine_cycle.py        _ml_df 초기화")
    print("  ✅  core/engine_ml.py           dashboard 임포트")
    print("  ✅  core/engine_sell.py         profit_rate 전달 + 들여쓰기")
    print("  ✅  core/engine_buy.py          안정성 개선")
    print("  ✅  signals/signal_combiner.py  가중치 통일")
    print("  ✅  strategies/v2/order_block_v2  open_arr 버그")
    print()
    print("  페이퍼 트레이딩 실행:")
    print("    python main.py --mode paper")
    print()
    print("  24시간 후 핵심 로그 확인:")
    print('    Select-String -Path logs\\*.log -Pattern "kelly|avg_win|CircuitBreaker"')
    print('    Select-String -Path logs\\*.log -Pattern "SELL|profit|손절|익절"')
