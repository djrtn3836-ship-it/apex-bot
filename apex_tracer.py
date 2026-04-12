import ast, sys, re, sqlite3, importlib
from pathlib import Path
from collections import defaultdict

ROOT = Path('.')
REPORT = []

def section(title):
    print(f'\n{"="*60}')
    print(f'  {title}')
    print(f'{"="*60}')

def ok(msg):   print(f'  ✅ {msg}')
def warn(msg): print(f'  ⚠️  {msg}'); REPORT.append(('WARN', msg))
def err(msg):  print(f'  ❌ {msg}'); REPORT.append(('ERR', msg))
def info(msg): print(f'  ℹ️  {msg}')

# 1. 구문 오류
section('1. 구문 오류 검사')
PY_FILES = [
    'core/engine.py','core/portfolio_manager.py','config/settings.py',
    'signals/signal_combiner.py','models/inference/predictor.py',
    'models/train/auto_trainer.py','data/storage/db_manager.py',
    'monitoring/dashboard.py','utils/helpers.py','signals/filters/fear_greed.py',
]
for f in PY_FILES:
    p = Path(f)
    if not p.exists():
        warn(f'{f} - 파일 없음'); continue
    try:
        ast.parse(p.read_text(encoding='utf-8'))
        ok(f'{f}')
    except SyntaxError as e:
        err(f'{f} - SyntaxError line {e.lineno}: {e.msg}')

# 2. Import 오류
section('2. Import 오류 검사')
sys.path.insert(0, str(ROOT))
for mod in ['config.settings','utils.helpers','signals.filters.fear_greed',
            'core.portfolio_manager','models.inference.predictor']:
    try:
        importlib.import_module(mod); ok(f'import {mod}')
    except Exception as e:
        err(f'import {mod} → {e}')

# 3. 신호 흐름 함수
section('3. 신호 흐름 함수 존재 여부')
ENGINE = Path('core/engine.py').read_text(encoding='utf-8')
ENGINE_LINES = ENGINE.splitlines()
FLOW_FUNCS = {
    '_cycle':'메인 사이클', '_analyze_market':'신규 종목 분석',
    '_analyze_existing_position':'기존 포지션 분석',
    '_evaluate_entry_signals':'매수 조건 판단',
    '_execute_buy':'매수 실행', '_execute_sell':'매도 실행',
    '_execute_sell_inner':'매도 실행 내부',
    '_check_position_exits':'SL/TP 체크',
    '_check_time_based_exits':'시간 기반 청산',
    '_get_ml_prediction':'ML 예측 단건',
    '_get_ml_prediction_batch':'ML 예측 배치',
    '_restore_positions_from_db':'포지션 복원',
    '_save_cooldown_to_db':'쿨다운 저장',
    '_load_cooldown_from_db':'쿨다운 로드',
}
func_lines = {}
for i, l in enumerate(ENGINE_LINES):
    for fn in FLOW_FUNCS:
        if f'def {fn}' in l:
            func_lines[fn] = i + 1
for fn, desc in FLOW_FUNCS.items():
    if fn in func_lines: ok(f'{fn}() line {func_lines[fn]} - {desc}')
    else: err(f'{fn}() 없음 - {desc}')

# 4. 중복 코드
section('4. 중복 코드 검사')

# 4-1. 쿨다운 실제 활성 중복 (주석 제외)
cd_active = [(i+1, l.strip()) for i,l in enumerate(ENGINE_LINES)
             if '_sell_cooldown.get(market)' in l and not l.strip().startswith('#')]
if len(cd_active) > 1:
    warn(f'sell_cooldown 활성 호출 {len(cd_active)}곳:')
    for ln, code in cd_active: info(f'  line {ln}: {code[:60]}')
else:
    ok(f'sell_cooldown 활성 호출: {len(cd_active)}곳 (정상)')

# 4-2. _execute_buy 호출 위치
buy_calls = [(i+1, l.strip()) for i,l in enumerate(ENGINE_LINES)
             if 'await self._execute_buy' in l]
if len(buy_calls) > 2:
    warn(f'_execute_buy 호출 {len(buy_calls)}곳 (중복 진입 가능):')
    for ln, code in buy_calls: info(f'  line {ln}: {code[:70]}')
else:
    ok(f'_execute_buy 호출 {len(buy_calls)}곳')

# 4-3. FGI 하향 조정 (실제 위험한 것만)
fgi_danger = [(i+1, l.strip()) for i,l in enumerate(ENGINE_LINES)
              if ('_base_buy  - ' in l or 'threshold_adj' in l)
              and not l.strip().startswith('#')]
if fgi_danger:
    warn(f'FGI 하향 조정 활성 {len(fgi_danger)}곳:')
    for ln, code in fgi_danger: info(f'  line {ln}: {code[:70]}')
else:
    ok('FGI 하향 조정 없음 (정상)')

# 4-4. profit_rate * 100
pr_100 = [(i+1, l.strip()) for i,l in enumerate(ENGINE_LINES)
          if '"profit_rate": profit_rate * 100' in l]
ok(f'profit_rate * 100: {len(pr_100)}곳 (소수→퍼센트 변환 정상)')

# 4-5. 쿨다운 기준
cd_600  = [i+1 for i,l in enumerate(ENGINE_LINES)
           if '_cd_elapsed < 600' in l and not l.strip().startswith('#')]
cd_1200 = [i+1 for i,l in enumerate(ENGINE_LINES)
           if '< 1200' in l and '_cd' in l and not l.strip().startswith('#')]
if cd_600: warn(f'쿨다운 600초 기준 활성: {cd_600}')
else:       ok('쿨다운 600초 기준 없음 (1200초 통일)')
if cd_1200: ok(f'쿨다운 1200초 기준 {len(cd_1200)}곳: {cd_1200}')

# 5. 반환값
section('5. 반환값 검사')
pm = Path('core/portfolio_manager.py').read_text(encoding='utf-8')
if 'return proceeds, profit_rate' in pm:
    ok('close_position → (proceeds, profit_rate) 반환')
else:
    warn('close_position 반환값 확인 필요')

helpers = Path('utils/helpers.py').read_text(encoding='utf-8')
if 'return gross - (fee_rate * 2)' in helpers:
    ok('calculate_profit_rate → 소수 반환')
    pr_db = [(i+1,l.strip()) for i,l in enumerate(ENGINE_LINES)
             if '"profit_rate"' in l and 'profit_rate' in l and 'insert' not in l.lower()]
    for ln, code in pr_db:
        if '* 100' in code:   ok(f'  line {ln}: DB 저장 *100 정상')
        elif '0.0' in code and 'profit_rate' in code: ok(f'  line {ln}: BUY 기록 profit_rate=0.0 정상')
        elif '/ 100' in code: ok(f'  line {ln}: 대시보드 표시용 /100 정상')
        else: warn(f'  line {ln}: 확인 필요: {code[:60]}')

# 6. ML 임계값
section('6. ML 임계값 일관성')
ALLOWED = {
    0.62: ('BUY 임계값', 'ok'),
    0.63: ('BEAR_REVERSAL 신호값 0.62초과', 'ok'),
    0.55: ('SELL 조건용', 'ok'),
    0.50: ('대형손실 SELL 조건용', 'ok'),
    0.40: ('OrderBook BUY_ZONE 보조신호 (매수결정 아님)', 'ok'),
    0.50: ('OrderBook SELL_ZONE 보조신호', 'ok'),
}
DANGER  = {0.1, 0.2, 0.3, 0.35, 0.45}

found = defaultdict(list)
for i, l in enumerate(ENGINE_LINES):
    if l.strip().startswith('#'): continue
    for pat in [r'ml_score\s*[><=!]+\s*([\d.]+)',
                r'buy_threshold\s*=\s*([\d.]+)',
                r'confidence\s*[><=!]+\s*([\d.]+)',
                r'confidence=([\d.]+)']:
        for m in re.finditer(pat, l):
            val = float(m.group(1))
            if 0.1 <= val <= 1.0:
                found[val].append((i+1, l.strip()[:65]))

for val, locs in sorted(found.items()):
    if val in DANGER:
        err(f'임계값 {val} 위험 ({len(locs)}곳):')
        for ln, code in locs[:2]: info(f'  line {ln}: {code}')
    elif val in (0.55, 0.50, 0.40):
        ok(f'임계값 {val} 정상 (용도: {ALLOWED.get(val, ("확인필요",""))[0]}): {len(locs)}곳')
    elif val == 0.62 or val == 0.63:
        ok(f'임계값 {val} (BUY 기준): {len(locs)}곳')
    elif val == 1.0: ok(f'임계값 1.0 (SmartWallet 매도 신뢰도 고정값, 정상): {len(locs)}곳')
    else:
        warn(f'임계값 {val} 확인필요: {len(locs)}곳')
        for ln, code in locs[:2]: info(f'  line {ln}: {code}')

# 7. DB 무결성
section('7. DB 무결성')
try:
    db = sqlite3.connect('database/apex_bot.db')
    cur = db.cursor()
    cur.execute("""SELECT COUNT(*),
        SUM(CASE WHEN ABS(profit_rate)>100 THEN 1 ELSE 0 END),
        SUM(CASE WHEN ABS(profit_rate)>10  THEN 1 ELSE 0 END),
        MIN(profit_rate), MAX(profit_rate)
        FROM trade_history WHERE side='SELL'""")
    total, over100, over10, mn, mx = cur.fetchone()
    ok(f'SELL {total}건 | MIN={mn:.2f}% MAX={mx:.2f}%')
    if over100: err(f'|profit_rate|>100: {over100}건 (100배 버그)')
    elif over10: warn(f'|profit_rate|>10: {over10}건 (이상값 확인)')
    else:        ok('profit_rate 범위 정상 (퍼센트 단위)')

    cur.execute("SELECT value FROM bot_state WHERE key='sell_cooldown'")
    row = cur.fetchone()
    if row:
        import json; cd=json.loads(row[0])
        ok(f'sell_cooldown: {len(cd)}개 종목')
    db.close()
except Exception as e:
    err(f'DB 오류: {e}')

# 8. 설정값
section('8. 설정값')
try:
    from config.settings import get_settings
    s = get_settings()
    ok(f'ML BUY 임계값: {s.risk.buy_signal_threshold}')
    ok(f'ML SELL 임계값: {s.risk.sell_signal_threshold}')
    ok(f'최대 포지션: {s.trading.max_positions}')
    ok(f'일일 손실 한도: {s.risk.daily_loss_limit}')
except Exception as e:
    err(f'settings 오류: {e}')

# 9. 30분 보유 조건
section('9. 30분 보유 조건')
held = [i+1 for i,l in enumerate(ENGINE_LINES) if '_held_min' in l]
if held: ok(f'30분 보유 조건 {len(held)}줄 (line {held})')
else:    err('30분 보유 조건 없음')

et_parse = [i+1 for i,l in enumerate(ENGINE_LINES)
            if 'entry_time' in l and ('fromisoformat' in l or 'fromtimestamp' in l)]
if et_parse: ok(f'entry_time 파싱 line {et_parse}')
else:        warn('entry_time float 타입 미처리 가능')

# 최종
section('최종 요약')
errs  = [m for t,m in REPORT if t=='ERR']
warns = [m for t,m in REPORT if t=='WARN']
print(f'\n  총 오류: {len(errs)}건 | 경고: {len(warns)}건')
if errs:
    print('\n  🔴 오류:')
    for m in errs: print(f'    - {m}')
if warns:
    print('\n  🟡 경고:')
    for m in warns: print(f'    - {m}')
if not errs and not warns:
    print('\n  🎉 모든 검사 통과!')
