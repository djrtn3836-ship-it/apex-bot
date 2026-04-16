
# APEX BOT 수정 규칙 및 핵심 메모
# 생성일: 2026-04-16
# 반드시 이 파일을 먼저 읽고 수정하세요

================================================================
## [규칙 1] profit_rate 단위 - 절대 헷갈리면 안 됨
================================================================

calculate_profit_rate() 반환값 = 소수 (예: 0.0355 = 3.55%)
  utils/helpers.py: return (current-entry)/entry - fee_rate*2

부분청산 (_execute_partial_sell):
  profit_rate = (sell가 - entry가) / entry가 * 100  ← 이미 % 단위
  DB 저장: "profit_rate": profit_rate  ← * 100 하면 안 됨

전량매도 (_execute_sell_inner):
  profit_rate = close_position() 반환값  ← 소수 단위
  DB 저장: "profit_rate": profit_rate * 100  ← 반드시 * 100 필요

PPO 트레이너: _pnl = profit_rate / 100  ← DB저장 후 소수로 변환

단위 검증 명령어 (수정 전 반드시 실행):
  python -c "from utils.helpers import calculate_profit_rate; print(calculate_profit_rate(1000,1020,0.001))"
  → 0.018 나오면 소수 단위 확인

수정 이력:
  FIX1 (2026-04-15): 부분청산 * 100 제거 ✅
  FIX2 (2026-04-15): 전량매도 * 100 제거 ❌ 잘못됨
  FIX3 (2026-04-16): 전량매도 * 100 복원 ✅

================================================================
## [규칙 2] NameError 수정 시 반드시 전체 파일 검색
================================================================

한 곳만 수정하면 다른 파일에 같은 버그 존재 가능
수정 전 반드시 전체 검색:
  python -c "
  import os
  for root,dirs,files in os.walk('.'):
      dirs[:] = [d for d in dirs if d not in ['__pycache__','.git','_archive_original']]
      for f in files:
          if not f.endswith('.py'): continue
          src = open(os.path.join(root,f),encoding='utf-8',errors='ignore').read()
          if '문제변수명' in src:
              print(os.path.relpath(os.path.join(root,f)))
  "

_UPBIT_VOL_PREC 수정 이력:
  FIX (2026-04-14): engine_utils.py 수정 ✅
  FIX (2026-04-15): engine_sell.py _ceil_vol도 수정 ✅

_dt / _ppo_dt import:
  engine_sell.py 상단에 반드시:
    import datetime as _dt
    import datetime as _ppo_dt

================================================================
## [규칙 3] 봇 재시작 절차 - 반드시 이 순서대로
================================================================

1. 반드시 관리자 PowerShell 사용
   (일반 PowerShell로 시작한 프로세스는 일반 PS로 종료 불가)

2. 정확한 재시작 순서:
   Get-ChildItem -Recurse -Filter "__pycache__" -Directory | Remove-Item -Recurse -Force
   taskkill /F /IM python.exe  ← 관리자 권한 필요
   Start-Sleep -Seconds 3
   Start-Process python.exe -ArgumentList "main.py --mode paper" -WorkingDirectory "경로" -WindowStyle Hidden
   Start-Sleep -Seconds 15
   Get-Process python | Select-Object Id, StartTime, CPU
   → PID 1개만 확인

3. 2개 이상이면:
   taskkill /PID 구버전PID /F
   또는 작업관리자 > 세부정보 > 우클릭 > 작업끝내기

================================================================
## [규칙 4] 패치 스크립트 작성 규칙
================================================================

1. 반드시 백업 먼저:
   shutil.copy(path, path + '.bak_YYYYMMDD')

2. 문자열 탐색 전 현재 코드 출력 후 확인:
   lines = open(path).readlines()
   for i,l in enumerate(lines[대상줄-3:대상줄+3], 대상줄-2):
       print(f'{i}| {l}', end='')

3. 탐색 실패 시 줄 번호로 직접 수정 (문자열 패턴 의존 금지)

4. 수정 후 반드시 문법 검증:
   ast.parse(new_src)

5. PowerShell here-string 규칙:
   @'...'@  ← 단순 문자열 (한글/이모지 포함 시 사용)
   @"..."@  ← 변수 치환 필요할 때만 사용
   이모지 포함 Python 코드는 반드시 파일로 저장 후 실행

================================================================
## [규칙 5] _signal_cooldown
================================================================

self._signal_cooldown = 240  ← 고정값 (settings.py에 없음)
BEAR_REVERSAL 마켓은 60초 적용
_last_signal_time은 체결 후에만 갱신 (BUY 시도 시 갱신 금지)

수정 이력:
  FIX (2026-04-14): 240초 고정, BUY시 갱신 제거 ✅

================================================================
## [규칙 6] paper 모드 SELL 수량
================================================================

SmartWallet = 실제 업비트 잔고 기준
paper 모드에서 실제 잔고 = 0 → DEAD/ORPHAN → SELL 차단

해결: paper 모드일 때 portfolio._positions에서 직접 수량 조회

수정 이력:
  FIX (2026-04-15): paper 분기 추가 ✅

================================================================
## [규칙 7] ML 익절 임계값
================================================================

최소 익절: signal=="SELL" and confidence>=0.65 and pnl_pct>=0.5
최소 손절: signal=="SELL" and confidence>=0.65 and pnl_pct<=-1.5
이 조건 없으면 +0.01% 수준 과매매 발생

수정 이력:
  FIX (2026-04-16): 임계값 강화 ✅

================================================================
## [규칙 8] 로그 확인 방법
================================================================

# 핵심 에러 찾을 때 (Last 숫자 크게):
Get-Content $log.FullName -Encoding UTF8 | Where-Object { $_ -match "키워드" } | Select-Object -Last 50

# 특정 시각 이후만:
Get-Content $log.FullName -Encoding UTF8 | Where-Object { $_ -match "2026-04-16T21:" -or $_ -match "키워드" }

# 에러/예외 전체:
Get-Content $log.FullName -Encoding UTF8 | Where-Object { $_ -match "Error|Exception|WARNING|NameError" }

================================================================
## [규칙 9] 수정 전 체크리스트
================================================================

□ APEX_BOT_RULES.md 읽었는가
□ 수정 대상 파일 백업했는가
□ 전체 파일 검색으로 같은 버그 다른 파일에 없는지 확인했는가
□ profit_rate 단위 확인했는가 (소수 vs %)
□ 패치 후 ast.parse 통과했는가
□ __pycache__ 삭제했는가
□ 재시작 후 PID 1개만 확인했는가
□ 2사이클(8분) 후 로그 확인했는가

================================================================
## [커밋 이력]
================================================================

d86ba86 (2026-04-14): BUY 신호 차단 8개 버그 수정
c8b1876 (2026-04-15): SELL profit_rate 이중곱셈 버그 수정
미커밋  (2026-04-16): profit_rate 단위 복원 + ML익절 임계값 강화

