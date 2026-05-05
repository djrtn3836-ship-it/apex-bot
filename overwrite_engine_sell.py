# overwrite_engine_sell.py
# engine_sell.py 전체를 완전 교정본으로 교체
# 수정 내용:
#   1. record_trade_result(is_win=..., profit_rate=.../100) — profit_rate 전달
#   2. log_trade / notify_sell / live_guard 콜백을 except 밖으로 올바르게 이동
#   3. log_trade / notify_sell 들여쓰기 정렬

import os, ast, shutil
from datetime import datetime

_TS  = datetime.now().strftime("%Y%m%d_%H%M%S")
_BAK = os.path.join("archive", f"overwrite_sell_{_TS}")
os.makedirs(_BAK, exist_ok=True)
print(f"\n📁 백업 경로: {_BAK}")

SELL_PATH = os.path.join("core", "engine_sell.py")

if not os.path.isfile(SELL_PATH):
    print(f"❌ 파일 없음: {SELL_PATH}")
    exit(1)

shutil.copy2(SELL_PATH, os.path.join(_BAK, "engine_sell.py.bak"))
print(f"   원본 백업 완료\n")

# ── 교정된 _execute_sell_inner 후반부 (record_trade_result 이후 전체) ──────────
# 전체 파일을 교체하지 않고, 문제 구간만 정확히 교체합니다.

with open(SELL_PATH, "r", encoding="utf-8") as f:
    src = f.read()

# 교체 대상: record_trade_result 호출부터 wallet.record_sell 끝까지
OLD_TAIL = '''            self.risk_manager.record_trade_result(profit_rate > 0)

            # V2 앙상블 동적 가중치 업데이트
            try:
                if hasattr(self, 'ensemble_engine') and self.ensemble_engine is not None:
                    _v2_strat_map = {
                        'Order_Block': 'OrderBlock_SMC',
                        'Vol_Breakout': 'VolBreakout',
                        'MACD_Cross': 'MACD_Cross',
                        'RSI_Divergence': 'RSI_Divergence',
                        'Bollinger_Squeeze': 'Bollinger_Squeeze',
                        'ATR_Channel': 'ATR_Channel',
                        'VWAP_Reversion': 'VWAP_Reversion',
                        'Supertrend': 'Supertrend',
                    }
                    _v2_name = _v2_strat_map.get(_strat_name, _strat_name)
                    self.ensemble_engine.update_result(_v2_name, profit_rate)
                    logger.debug(f'[Ensemble] update_result {_v2_name} {profit_rate:+.2f}%')
            except Exception as _ue:
                logger.debug(f'[Ensemble] update_result 오류(무시): {_ue}')

        # ── 전략별 개별 쿨다운 (Per-Strategy Cooldown) ──────────────
        try:
            _strat_cd_rules = {
                "Vol_Breakout":    {"max_loss": 2, "hours": 1},
                "VWAP_Reversion":  {"max_loss": 3, "hours": 1},
            }
            if not hasattr(self, "_strat_consec_loss"):
                self._strat_consec_loss   = {}   # {전략명: 연속손실횟수}
            if not hasattr(self, "_strat_cooldown_until"):
                self._strat_cooldown_until = {}  # {전략명: 만료datetime}
            _cur_strat = _strat_name if '_strat_name' in dir() else 'unknown'  # FIX: 스코프 수정
            if _cur_strat in _strat_cd_rules:
                _rule = _strat_cd_rules[_cur_strat]
                if profit_rate < 0:
                    self._strat_consec_loss[_cur_strat] = (
                        self._strat_consec_loss.get(_cur_strat, 0) + 1)
                    _cnt = self._strat_consec_loss[_cur_strat]
                    if _cnt >= _rule["max_loss"]:
                        import datetime as _pcd_dt
                        _until = _pcd_dt.datetime.now() + _pcd_dt.timedelta(hours=_rule["hours"])
                        self._strat_cooldown_until[_cur_strat] = _until
                        logger.warning(
                            f"[STRAT-CD] {_cur_strat} {_cnt}연속손실 "
                            f"→ {_rule['hours']}h 냉각 (until {_until.strftime('%H:%M')})"
                        )
                        self._strat_consec_loss[_cur_strat] = 0  # 카운터 리셋
                else:
                    self._strat_consec_loss[_cur_strat] = 0  # 수익 시 리셋
        except Exception as _scd_e:
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
            )

        try:
            _sold_qty = float(getattr(result, "executed_volume",
                        getattr(result, "quantity",
                        getattr(result, "qty", _wallet_sell_qty))))
            if _sold_qty > 0:
                self._wallet.record_sell(
                    symbol=_symbol, sold_qty=_sold_qty,
                    includes_dust=_wallet_incl_dust,
                )
        except Exception as _we:
            logger.debug(f"SmartWallet record_sell : {_we}")
    # ── 초기화 헬퍼 ─────────────────────────────────────────────'''

NEW_TAIL = '''            # [BUG-REAL-1-C FIX] profit_rate(%) 를 소수로 변환해 함께 전달
            self.risk_manager.record_trade_result(
                is_win=profit_rate > 0,
                profit_rate=profit_rate / 100.0,  # % → 소수 변환
            )

            # V2 앙상블 동적 가중치 업데이트
            try:
                if hasattr(self, 'ensemble_engine') and self.ensemble_engine is not None:
                    _v2_strat_map = {
                        'Order_Block': 'OrderBlock_SMC',
                        'Vol_Breakout': 'VolBreakout',
                        'MACD_Cross': 'MACD_Cross',
                        'RSI_Divergence': 'RSI_Divergence',
                        'Bollinger_Squeeze': 'Bollinger_Squeeze',
                        'ATR_Channel': 'ATR_Channel',
                        'VWAP_Reversion': 'VWAP_Reversion',
                        'Supertrend': 'Supertrend',
                    }
                    _v2_name = _v2_strat_map.get(_strat_name, _strat_name)
                    self.ensemble_engine.update_result(_v2_name, profit_rate)
                    logger.debug(f'[Ensemble] update_result {_v2_name} {profit_rate:+.2f}%')
            except Exception as _ue:
                logger.debug(f'[Ensemble] update_result 오류(무시): {_ue}')

        # ── 전략별 개별 쿨다운 (Per-Strategy Cooldown) ──────────────
        try:
            _strat_cd_rules = {
                "Vol_Breakout":    {"max_loss": 2, "hours": 1},
                "VWAP_Reversion":  {"max_loss": 3, "hours": 1},
            }
            if not hasattr(self, "_strat_consec_loss"):
                self._strat_consec_loss   = {}
            if not hasattr(self, "_strat_cooldown_until"):
                self._strat_cooldown_until = {}
            _cur_strat = _strat_name if '_strat_name' in dir() else 'unknown'
            if _cur_strat in _strat_cd_rules:
                _rule = _strat_cd_rules[_cur_strat]
                if profit_rate < 0:
                    self._strat_consec_loss[_cur_strat] = (
                        self._strat_consec_loss.get(_cur_strat, 0) + 1)
                    _cnt = self._strat_consec_loss[_cur_strat]
                    if _cnt >= _rule["max_loss"]:
                        import datetime as _pcd_dt
                        _until = _pcd_dt.datetime.now() + _pcd_dt.timedelta(hours=_rule["hours"])
                        self._strat_cooldown_until[_cur_strat] = _until
                        logger.warning(
                            f"[STRAT-CD] {_cur_strat} {_cnt}연속손실 "
                            f"→ {_rule['hours']}h 냉각 (until {_until.strftime('%H:%M')})"
                        )
                        self._strat_consec_loss[_cur_strat] = 0
                else:
                    self._strat_consec_loss[_cur_strat] = 0
        except Exception as _scd_e:
            logger.debug(f"[STRAT-CD] 처리 중 오류(무시): {_scd_e}")
        # ── 전략별 개별 쿨다운 끝 ────────────────────────────────────

        # [LiveGuard] 매도 결과 콜백 — 연속 손실 추적 (except 블록 밖에서 실행)
        try:
            if hasattr(self, 'live_guard') and self.live_guard is not None:
                await self.live_guard.on_trade_result(profit_rate / 100.0, market)
                if profit_rate < 0:
                    _prev = getattr(self.live_guard, '_today_loss_pct', 0.0)
                    self.live_guard._today_loss_pct = _prev + (profit_rate / 100.0)
        except Exception as _lg_e:
            logger.debug(f'[LiveGuard] on_trade_result 호출 실패: {_lg_e}')

        # [FIX-INDENT] log_trade / notify_sell — except 블록 밖에서 항상 실행
        log_trade(
            "SELL", market, result.executed_price,
            proceeds, reason, profit_rate
        )
        await self.telegram.notify_sell(
            market, result.executed_price, result.executed_volume,
            profit_rate, reason
        )

        try:
            _sold_qty = float(getattr(result, "executed_volume",
                        getattr(result, "quantity",
                        getattr(result, "qty", _wallet_sell_qty))))
            if _sold_qty > 0:
                self._wallet.record_sell(
                    symbol=_symbol, sold_qty=_sold_qty,
                    includes_dust=_wallet_incl_dust,
                )
        except Exception as _we:
            logger.debug(f"SmartWallet record_sell : {_we}")
    # ── 초기화 헬퍼 ─────────────────────────────────────────────'''

print("=" * 60)
print("[OVERWRITE] core/engine_sell.py 교정 구간 교체 중...")

if OLD_TAIL not in src:
    print("  ❌ 패턴 불일치 — 이미 로컬에서 일부 수정된 상태입니다.")
    print("  → 로컬 파일의 현재 record_trade_result 호출 확인:")

    # 로컬에서 이미 patch_sell_record.py 가 적용됐는지 체크
    already_patched = "profit_rate / 100.0" in src
    indent_fixed    = "        log_trade(\n            \"SELL\"" in src

    print(f"     profit_rate 전달: {'✅ 적용됨' if already_patched else '❌ 미적용'}")
    print(f"     들여쓰기 교정:    {'✅ 적용됨' if indent_fixed else '❌ 미적용'}")

    if already_patched and not indent_fixed:
        print()
        print("  → patch_sell_record.py 는 적용됐으나 들여쓰기 버그만 남아 있습니다.")
        print("  → 아래 OLD_TAIL_V2 패턴으로 재시도합니다...")

        # patch_sell_record.py 적용 후의 패턴으로 재시도
        OLD_TAIL_V2 = '''            # [BUG-REAL-1-C FIX] profit_rate(%) 를 소수로 변환해 함께 전달
            self.risk_manager.record_trade_result(
                is_win=profit_rate > 0,
                profit_rate=profit_rate / 100.0,  # % → 소수 변환
            )

            # V2 앙상블 동적 가중치 업데이트
            try:
                if hasattr(self, 'ensemble_engine') and self.ensemble_engine is not None:
                    _v2_strat_map = {
                        'Order_Block': 'OrderBlock_SMC',
                        'Vol_Breakout': 'VolBreakout',
                        'MACD_Cross': 'MACD_Cross',
                        'RSI_Divergence': 'RSI_Divergence',
                        'Bollinger_Squeeze': 'Bollinger_Squeeze',
                        'ATR_Channel': 'ATR_Channel',
                        'VWAP_Reversion': 'VWAP_Reversion',
                        'Supertrend': 'Supertrend',
                    }
                    _v2_name = _v2_strat_map.get(_strat_name, _strat_name)
                    self.ensemble_engine.update_result(_v2_name, profit_rate)
                    logger.debug(f'[Ensemble] update_result {_v2_name} {profit_rate:+.2f}%')
            except Exception as _ue:
                logger.debug(f'[Ensemble] update_result 오류(무시): {_ue}')

        # ── 전략별 개별 쿨다운 (Per-Strategy Cooldown) ──────────────
        try:
            _strat_cd_rules = {
                "Vol_Breakout":    {"max_loss": 2, "hours": 1},
                "VWAP_Reversion":  {"max_loss": 3, "hours": 1},
            }
            if not hasattr(self, "_strat_consec_loss"):
                self._strat_consec_loss   = {}   # {전략명: 연속손실횟수}
            if not hasattr(self, "_strat_cooldown_until"):
                self._strat_cooldown_until = {}  # {전략명: 만료datetime}
            _cur_strat = _strat_name if '_strat_name' in dir() else 'unknown'  # FIX: 스코프 수정
            if _cur_strat in _strat_cd_rules:
                _rule = _strat_cd_rules[_cur_strat]
                if profit_rate < 0:
                    self._strat_consec_loss[_cur_strat] = (
                        self._strat_consec_loss.get(_cur_strat, 0) + 1)
                    _cnt = self._strat_consec_loss[_cur_strat]
                    if _cnt >= _rule["max_loss"]:
                        import datetime as _pcd_dt
                        _until = _pcd_dt.datetime.now() + _pcd_dt.timedelta(hours=_rule["hours"])
                        self._strat_cooldown_until[_cur_strat] = _until
                        logger.warning(
                            f"[STRAT-CD] {_cur_strat} {_cnt}연속손실 "
                            f"→ {_rule['hours']}h 냉각 (until {_until.strftime('%H:%M')})"
                        )
                        self._strat_consec_loss[_cur_strat] = 0  # 카운터 리셋
                else:
                    self._strat_consec_loss[_cur_strat] = 0  # 수익 시 리셋
        except Exception as _scd_e:
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
            )

        try:
            _sold_qty = float(getattr(result, "executed_volume",
                        getattr(result, "quantity",
                        getattr(result, "qty", _wallet_sell_qty))))
            if _sold_qty > 0:
                self._wallet.record_sell(
                    symbol=_symbol, sold_qty=_sold_qty,
                    includes_dust=_wallet_incl_dust,
                )
        except Exception as _we:
            logger.debug(f"SmartWallet record_sell : {_we}")
    # ── 초기화 헬퍼 ─────────────────────────────────────────────'''

        if OLD_TAIL_V2 in src:
            src = src.replace(OLD_TAIL_V2, NEW_TAIL, 1)
            print("  ✅ V2 패턴으로 교체 성공")
        else:
            print("  ❌ V2 패턴도 불일치 — git push 후 재실행하거나 수동 수정 필요")
            print()
            print("  수동 수정 방법: core/engine_sell.py 에서")
            print("  'except Exception as _scd_e:' 블록 안의 아래 코드를")
            print("  except 블록 바깥(같은 수준)으로 꺼내세요:")
            print("    - live_guard.on_trade_result 호출 블록")
            print("    - log_trade(...) 호출")
            print("    - await self.telegram.notify_sell(...) 호출")
            exit(1)
    elif already_patched and indent_fixed:
        print()
        print("  ✅ 이미 모든 수정이 적용된 상태입니다. 추가 작업 불필요.")
        exit(0)
    else:
        print("  ❌ 패턴 완전 불일치 — git push 후 재실행 권장")
        exit(1)
else:
    src = src.replace(OLD_TAIL, NEW_TAIL, 1)
    print("  ✅ 원본 패턴으로 교체 성공")

# ── 문법 검사 후 저장 ────────────────────────────────────────────────────────
try:
    ast.parse(src)
except SyntaxError as e:
    print(f"  ❌ 문법 오류 — 원본 유지: {e}")
    exit(1)

with open(SELL_PATH, "w", encoding="utf-8") as f:
    f.write(src)

# ── 최종 검증 ────────────────────────────────────────────────────────────────
with open(SELL_PATH, "r", encoding="utf-8") as f:
    verify = f.read()

c1 = "profit_rate / 100.0" in verify
c2 = "        log_trade(\n            \"SELL\"" in verify
c3 = "        await self.telegram.notify_sell(" in verify
c4 = "        # [LiveGuard]" in verify

print()
print("=" * 60)
print("🔍 최종 검증")
print("=" * 60)
print(f"  {'✅' if c1 else '❌'}  profit_rate 동적 Kelly 전달")
print(f"  {'✅' if c2 else '❌'}  log_trade 들여쓰기 교정")
print(f"  {'✅' if c3 else '❌'}  notify_sell 들여쓰기 교정")
print(f"  {'✅' if c4 else '❌'}  LiveGuard except 블록 밖으로 이동")

if all([c1, c2, c3, c4]):
    print()
    print("✅ engine_sell.py 완전 교정 완료!")
    print()
    print("  이제 git push 후 페이퍼 트레이딩을 시작하세요:")
    print()
    print("  git add -A")
    print('  git commit -m "fix: engine_sell.py 들여쓰기 버그 + profit_rate 전달 교정"')
    print("  git push origin main")
    print()
    print("  python main.py --mode paper")
else:
    print()
    print("⚠️  일부 항목 미적용 — git push 후 재실행하거나 수동 확인 필요")
