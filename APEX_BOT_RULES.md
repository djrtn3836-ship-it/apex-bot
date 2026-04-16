
# APEX BOT 수정 규칙 및 핵심 메모
# 생성일: 2026-04-16
# 이 파일을 반드시 읽고 수정하세요

================================================================
## [규칙 1] profit_rate 단위 - 절대 헷갈리면 안 됨
================================================================

calculate_profit_rate() 반환값 = 소수 (예: 0.0355 = 3.55%)
  - utils/helpers.py: return gross - (fee_rate * 2)
  - gross = (current - entry) / entry  ← 소수

부분청산 (_execute_partial_sell):
  profit_rate = (sell가 - entry가) / entry가 * 100  ← 이미 % 단위
  DB 저장: "profit_rate": profit_rate  ← * 100 하면 안 됨 (100배 뻥튀기)

전량매도 (_execute_sell_inner):
  profit_rate = close_position() 반환값  ← 소수 단위
  DB 저장: "profit_rate": profit_rate * 100  ← 반드시 * 100 필요

PPO 트레이너 입력:
  _pnl = profit_rate / 100  ← 소수로 변환 (DB 저장 후 처리이므로 % → 소수)

검증 방법:
  python -c "from utils.helpers import calculate_profit_rate; print(calculate_profit_rate(1000,1020,0.001))"
  → 0.018 나오면 소수 단위 확인

수정 이력:
  FIX1 (2026-04-15): 부분청산 * 100 제거 ✅ 올바름
  FIX2 (2026-04-15): 전량매도 * 100 제거 ❌ 잘못됨 (주석 오해)
  FIX3 (2026-04-16): 전량매도 * 100 복원 ✅ 올바름

================================================================
## [규칙 2] _signal_cooldown - BUY 차단 주의
================================================================

self._signal_cooldown = 240  ← 고정값 (settings.py에 없음)
  - 이 값이 크면 BUY 신호가 영원히 차단됨
  - BEAR_REVERSAL 마켓은 60초 적용
  - _last_signal_time은 체결 후에만 갱신 (BUY 시도 시 갱신 금지)

수정 이력:
  FIX (2026-04-14): _signal_cooldown 240초 고정, BUY시 갱신 제거 ✅

================================================================
## [규칙 3] _UPBIT_VOL_PREC - NameError 주의
================================================================

_UPBIT_VOL_PREC는 engine.py에만 정의됨
engine_utils.py, engine_sell.py에서 직접 참조 금지

올바른 방법:
  def _floor_vol(market, volume):
      _PREC_MAP = {"KRW-BTC":8, "KRW-ETH":8, ...}  ← 내부 정의
      prec = _PREC_MAP.get(market, 4)
      return int(volume * 10**prec) / 10**prec

  def _ceil_vol(market, vol):
      import math
      _PREC_MAP = {"KRW-BTC":8, ...}
      return math.ceil(vol * 10**prec) / 10**prec

수정 이력:
  FIX (2026-04-14~15): engine_utils.py, engine_sell.py 교체 ✅

================================================================
## [규칙 4] ML 익절 임계값
================================================================

최소 익절 조건 (engine_cycle.py):
  signal == "SELL" and confidence >= 0.65 and pnl_pct >= 0.5   ← 익절
  signal == "SELL" and confidence >= 0.65 and pnl_pct <= -1.5  ← 손절
  이 조건 없으면 +0.01% 수준 과매매 발생 (CFG, DEEP 반복매매)

수정 이력:
  FIX (2026-04-16): 임계값 강화 ✅

================================================================
## [규칙 5] _dt / _ppo_dt import
================================================================

engine_sell.py 상단에 반드시 있어야 함:
  import datetime as _dt
  import datetime as _ppo_dt

없으면 SELL DB 저장 시 NameError 발생

수정 이력:
  FIX (2026-04-15): 상단 import 추가 ✅

================================================================
## [규칙 6] paper 모드 SELL 수량
================================================================

SmartWallet은 실제 업비트 잔고 기준으로 작동
paper 모드에서는 실제 잔고 = 0 → DEAD/ORPHAN 처리

해결: engine_sell.py _execute_sell_inner에서
  paper 모드일 때 portfolio._positions에서 직접 수량 조회
  SmartWallet 완전 우회

수정 이력:
  FIX (2026-04-15): paper 분기 추가 ✅

================================================================
## [규칙 7] 수정 전 반드시 확인할 것
================================================================

1. 수정 전 백업: shutil.copy(path, path + '.bak_YYYYMMDD')
2. 수정 후 문법 검증: ast.parse(new_src)
3. 수정 후 재시작 전 __pycache__ 삭제
4. 재시작 후 반드시 로그 확인 (최소 2사이클 = 8분)
5. profit_rate 수정 시 반드시 단위 검증 스크립트 실행

================================================================
## [커밋 이력]
================================================================

d86ba86 (2026-04-14): BUY 신호 차단 8개 버그 수정
c8b1876 (2026-04-15): SELL profit_rate 이중곱셈 버그 수정
미커밋  (2026-04-16): profit_rate 단위 최종 복원 + ML익절 임계값 강화

