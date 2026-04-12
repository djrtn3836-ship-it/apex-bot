import ast, sys, re, sqlite3, importlib, subprocess
from pathlib import Path
from collections import defaultdict
from datetime import datetime

ROOT = Path('.')
sys.path.insert(0, str(ROOT))
REPORT = []

def section(title):
    print(f'\n{"="*60}')
    print(f'  {title}')
    print(f'{"="*60}')

def ok(msg):    print(f'  ✅ {msg}')
def warn(msg):  print(f'  ⚠️  {msg}'); REPORT.append(('WARN', msg))
def err(msg):   print(f'  ❌ {msg}'); REPORT.append(('ERR',  msg))
def info(msg):  print(f'  ℹ️  {msg}')

ENGINE_TEXT  = Path('core/engine.py').read_text(encoding='utf-8')
ENGINE_LINES = ENGINE_TEXT.splitlines()

# ────────────────────────────────────────────────────────────
section('1. 구문 오류 (전체 py 파일)')
# ────────────────────────────────────────────────────────────
PY_FILES = [
    'core/engine.py','core/portfolio_manager.py',
    'config/settings.py','signals/signal_combiner.py',
    'models/inference/predictor.py','models/train/auto_trainer.py',
    'data/storage/db_manager.py','monitoring/dashboard.py',
    'utils/helpers.py','signals/filters/fear_greed.py',
    'risk/stop_loss/atr_stop.py','risk/stop_loss/trailing_stop.py',
]
for f in PY_FILES:
    p = Path(f)
    if not p.exists(): warn(f'{f} 파일 없음'); continue
    try:
        ast.parse(p.read_text(encoding='utf-8')); ok(f'{f}')
    except SyntaxError as e:
        err(f'{f} SyntaxError line {e.lineno}: {e.msg}')

# ────────────────────────────────────────────────────────────
section('2. Import 오류')
# ────────────────────────────────────────────────────────────
IMPORTS = [
    'config.settings','utils.helpers','signals.filters.fear_greed',
    'core.portfolio_manager','models.inference.predictor',
    'signals.signal_combiner','data.storage.db_manager',
]
for mod in IMPORTS:
    try:
        importlib.import_module(mod); ok(f'import {mod}')
    except Exception as e:
        err(f'import {mod} → {type(e).__name__}: {e}')

# ────────────────────────────────────────────────────────────
section('3. 신호 흐름 함수 존재 및 라인')
# ────────────────────────────────────────────────────────────
FLOW = {
    '_cycle':                     '메인 사이클',
    '_analyze_market':            '신규 종목 분석',
    '_analyze_existing_position': '기존 포지션 분석',
    '_evaluate_entry_signals':    '매수 조건 판단',
    '_execute_buy':               '매수 실행',
    '_execute_sell':              '매도 실행',
    '_execute_sell_inner':        '매도 실행 내부',
    '_check_position_exits':      'SL/TP 체크',
    '_check_time_based_exits':    '시간 기반 청산',
    '_get_ml_prediction':         'ML 예측 단건',
    '_get_ml_prediction_batch':   'ML 예측 배치',
    '_restore_positions_from_db': '포지션 복원',
    '_save_cooldown_to_db':       '쿨다운 저장',
    '_load_cooldown_from_db':     '쿨다운 로드',
}
func_map = {}
for i, l in enumerate(ENGINE_LINES):
    for fn in FLOW:
        if f'def {fn}' in l: func_map[fn] = i+1
for fn, desc in FLOW.items():
    if fn in func_map: ok(f'{fn}() line {func_map[fn]} – {desc}')
    else: err(f'{fn}() 없음 – {desc}')

# ────────────────────────────────────────────────────────────
section('4. 매수 신호 흐름 추적')
# ────────────────────────────────────────────────────────────

# 4-1. ML 배치 루프 진입 임계값
ml_entries = [(i+1, l.strip()) for i,l in enumerate(ENGINE_LINES)
              if re.search(r'ml_score\s*[><=!]+\s*[\d.]+', l)
              and not l.strip().startswith('#')]
for ln, code in ml_entries:
    m = re.search(r'ml_score\s*(?:>=|<=|==|!=|>|<)\s*([\d.]+)', code)
    if m:
        val = float(m.group(1))
        if val < 0.5:
            err(f'ML 진입 임계값 {val} 너무 낮음 – line {ln}: {code[:60]}')
        else:
            ok(f'ML 진입 임계값 {val} – line {ln}')

# 4-2. _execute_buy 호출 위치 (중복 진입)
buy_calls = [(i+1, l.strip()) for i,l in enumerate(ENGINE_LINES)
             if 'await self._execute_buy' in l]
if len(buy_calls) > 2:
    warn(f'_execute_buy {len(buy_calls)}곳 호출 (중복 진입 가능):')
    for ln, code in buy_calls: info(f'  line {ln}: {code[:70]}')
else:
    ok(f'_execute_buy 호출 {len(buy_calls)}곳')

# 4-3. 쿨다운 활성 블록
cd_active = [(i+1, l.strip()) for i,l in enumerate(ENGINE_LINES)
             if '_sell_cooldown.get(market)' in l
             and not l.strip().startswith('#')]
if len(cd_active) > 1:
    warn(f'sell_cooldown 활성 {len(cd_active)}곳 (중복):')
    for ln, code in cd_active: info(f'  line {ln}: {code[:60]}')
else:
    ok(f'sell_cooldown 활성 {len(cd_active)}곳')

# 4-4. 쿨다운 기준값 통일
cd_600  = [i+1 for i,l in enumerate(ENGINE_LINES)
           if '_cd_elapsed < 600' in l and not l.strip().startswith('#')]
cd_1200 = [i+1 for i,l in enumerate(ENGINE_LINES)
           if re.search(r'total_seconds\(\)\s*<\s*1200', l)
           and not l.strip().startswith('#')]
if cd_600:  err(f'쿨다운 600초 기준 활성: {cd_600}')
else:        ok('쿨다운 기준 1200초 통일')

# 4-5. FGI 하향 조정 활성 여부
fgi_down = [(i+1, l.strip()) for i,l in enumerate(ENGINE_LINES)
            if ('_base_buy  - ' in l or 'threshold_adj' in l)
            and not l.strip().startswith('#')]
if fgi_down:
    err(f'FGI 하향 조정 활성 {len(fgi_down)}곳:')
    for ln, code in fgi_down: info(f'  line {ln}: {code[:70]}')
else:
    ok('FGI 하향 조정 비활성 (고정 0.62)')

# 4-6. BEAR_REVERSAL 신호 confidence 값
bear_conf = []  # 아래 루프로 처리
for i,l in enumerate(ENGINE_LINES):
    if 'contributing_strategies=["BEAR_REVERSAL"]' in l or "contributing_strategies=['BEAR_REVERSAL']" in l:
        # 앞뒤 5줄에서 confidence 값 확인
        for j in range(max(0,i-5), min(len(ENGINE_LINES),i+5)):
            m = re.search(r'confidence=([\d.]+)', ENGINE_LINES[j])
            if m:
                val = float(m.group(1))
                if val >= 0.62:
                    ok(f'BEAR_REVERSAL confidence={val} (0.62 이상) line {j+1}')
                else:
                    err(f'BEAR_REVERSAL confidence={val} (0.62 미달) line {j+1}')
                break

# ────────────────────────────────────────────────────────────
section('5. 매도 신호 흐름 추적')
# ────────────────────────────────────────────────────────────

# 5-1. 30분 보유 조건
held = [(i+1, l.strip()) for i,l in enumerate(ENGINE_LINES) if '_held_min' in l]
if len(held) >= 3:
    ok(f'30분 보유 조건 {len(held)}줄')
    for ln, code in held: info(f'  line {ln}: {code[:70]}')
else:
    err('30분 보유 조건 부족')

# 5-2. entry_time 파싱 (float/str 모두 처리)
has_fromisoformat  = any('fromisoformat' in l for l in ENGINE_LINES)
has_fromtimestamp  = any('fromtimestamp' in l for l in ENGINE_LINES)
if has_fromisoformat and has_fromtimestamp:
    ok('entry_time 파싱: str(fromisoformat) + float(fromtimestamp) 모두 처리')
elif has_fromisoformat:
    warn('entry_time: fromisoformat만 있음 (float 타입 미처리 가능)')
else:
    err('entry_time 파싱 로직 없음')

# 5-3. SL 체크 경로
sl_checks = [(i+1, l.strip()) for i,l in enumerate(ENGINE_LINES)
             if 'current_price <= ' in l and 'sl' in l.lower()
             and not l.strip().startswith('#')]
ok(f'SL 체크 포인트: {len(sl_checks)}곳')
for ln, code in sl_checks: info(f'  line {ln}: {code[:70]}')

# 5-4. profit_rate DB 저장 단위
pr_save = [(i+1, l.strip()) for i,l in enumerate(ENGINE_LINES)
           if '"profit_rate"' in l and 'profit_rate' in l]
for ln, code in pr_save:
    if '* 100' in code:
        ok(f'  line {ln}: SELL DB저장 *100 정상')
    elif '= 0.0' in code or '0.0,' in code:
        ok(f'  line {ln}: BUY DB저장 0.0 정상')
    elif '/ 100' in code:
        ok(f'  line {ln}: 대시보드 /100 정상')
    else:
        warn(f'  line {ln}: 확인필요 → {code[:60]}')

# ────────────────────────────────────────────────────────────
section('6. ML 임계값 일관성 전수조사')
# ────────────────────────────────────────────────────────────
KNOWN_OK = {
    0.62: 'BUY 임계값',
    0.63: 'BEAR_REVERSAL 신호값',
    0.55: 'SELL 조건 (pnl>=1%)',
    0.50: 'SELL 조건 (대형손실)',
    0.40: 'OrderBook BUY_ZONE 보조',
    1.00: 'SmartWallet 고정값',
}
DANGER_VALS = {0.1, 0.2, 0.3, 0.35, 0.45}

found_vals = defaultdict(list)
for i, l in enumerate(ENGINE_LINES):
    if l.strip().startswith('#'): continue
    for pat in [r'ml_score\s*(?:>=|<=|==|!=|>|<)\s*([\d.]+)',
                r'confidence\s*[><=!]+\s*([\d.]+)',
                r'confidence=([\d.]+)',
                r'buy_threshold\s*=\s*([\d.]+)']:
        for m in re.finditer(pat, l):
            val = float(m.group(1))
            if 0.1 <= val <= 1.0:
                found_vals[val].append((i+1, l.strip()[:65]))

for val in sorted(found_vals):
    locs = found_vals[val]
    if val in DANGER_VALS:
        err(f'위험 임계값 {val} ({len(locs)}곳):')
        for ln, code in locs[:3]: info(f'  line {ln}: {code}')
    elif val in KNOWN_OK:
        ok(f'임계값 {val} ({KNOWN_OK[val]}): {len(locs)}곳')
    else:
        warn(f'임계값 {val} 용도 확인필요: {len(locs)}곳')
        for ln, code in locs[:2]: info(f'  line {ln}: {code}')

# ────────────────────────────────────────────────────────────
section('7. DB 무결성')
# ────────────────────────────────────────────────────────────
try:
    db  = sqlite3.connect('database/apex_bot.db')
    cur = db.cursor()

    cur.execute("""SELECT COUNT(*),
        SUM(CASE WHEN ABS(profit_rate)>100 THEN 1 ELSE 0 END),
        SUM(CASE WHEN ABS(profit_rate)>10  THEN 1 ELSE 0 END),
        ROUND(MIN(profit_rate),4), ROUND(MAX(profit_rate),4)
        FROM trade_history WHERE side='SELL'""")
    total, ov100, ov10, mn, mx = cur.fetchone()
    ok(f'SELL 총 {total}건 | MIN={mn}% MAX={mx}%')
    if ov100: err(f'|profit_rate|>100: {ov100}건 ← 100배 버그')
    elif ov10: warn(f'|profit_rate|>10: {ov10}건 확인 필요')
    else:      ok('profit_rate 범위 정상')

    # 새 설정 이후 데이터
    cur.execute("""SELECT COUNT(*),
        ROUND(AVG(profit_rate),4),
        ROUND(SUM(CASE WHEN profit_rate>0 THEN 1 ELSE 0 END)*100.0/COUNT(*),1)
        FROM trade_history
        WHERE side='SELL' AND timestamp >= '2026-04-11T14:30:00'""")
    r = cur.fetchone()
    ok(f'새 설정 이후 SELL {r[0]}건 | 평균PnL {r[1]}% | 승률 {r[2]}%')
    if r[2] and r[2] < 40:
        warn(f'승률 {r[2]}% (목표 55%)')
    elif r[2]:
        ok(f'승률 {r[2]}%')

    cur.execute("SELECT value FROM bot_state WHERE key='sell_cooldown'")
    row = cur.fetchone()
    if row:
        import json; cd = json.loads(row[0])
        ok(f'sell_cooldown DB: {len(cd)}개 종목')
    db.close()
except Exception as e:
    err(f'DB 오류: {e}')

# ────────────────────────────────────────────────────────────
section('8. 설정값 검증')
# ────────────────────────────────────────────────────────────
try:
    from config.settings import get_settings
    s = get_settings()
    checks = [
        ('ML BUY 임계값',    s.risk.buy_signal_threshold,  0.62),
        ('ML SELL 임계값',   s.risk.sell_signal_threshold, 0.55),
        ('최대 포지션',      s.trading.max_positions,      10),
        ('일일 손실 한도',   s.risk.daily_loss_limit,      0.05),
        ('총 드로다운 한도', s.risk.total_drawdown_limit,  0.10),
    ]
    for name, val, target in checks:
        if val == target: ok(f'{name}: {val}')
        else: warn(f'{name}: {val} (권장 {target})')
    ok(f'ATR SL 배수: {s.risk.atr_stop_multiplier}')
    ok(f'ATR TP 배수: {s.risk.atr_target_multiplier}')
except Exception as e:
    err(f'settings 오류: {e}')

# ────────────────────────────────────────────────────────────
section('9. 봇 프로세스 및 로그 상태')
# ────────────────────────────────────────────────────────────
try:
    result = subprocess.run(['tasklist','/FI','IMAGENAME eq python.exe'],
                            capture_output=True, text=True)
    proc_count = result.stdout.count('python.exe')
    if proc_count >= 2: ok(f'봇 프로세스: {proc_count}개 실행 중')
    elif proc_count == 1: warn(f'봇 프로세스: {proc_count}개 (2개 권장)')
    else: err('봇 프로세스 없음')
except: warn('프로세스 확인 불가')

log_files = sorted(Path('logs').glob('*.log'), key=lambda f: f.stat().st_mtime, reverse=True)
if log_files:
    lf = log_files[0]
    age = (datetime.now().timestamp() - lf.stat().st_mtime)
    if age < 120: ok(f'로그 갱신: {age:.0f}초 전')
    else: warn(f'로그 갱신: {age:.0f}초 전 (봇 중단 가능)')
    lines_log = lf.read_text(encoding='utf-8', errors='ignore').splitlines()
    errors   = [l for l in lines_log[-500:] if 'ERROR' in l]
    warnings = [l for l in lines_log[-500:] if 'WARNING' in l and '429' not in l]
    api_429  = sum(1 for l in lines_log[-500:] if '429' in l)
    if errors:   err(f'최근 ERROR {len(errors)}건')
    else:         ok('최근 ERROR 0건')
    if warnings: warn(f'최근 WARNING {len(warnings)}건 (429 제외)')
    else:         ok('최근 WARNING 0건')
    ok(f'API 429: {api_429}건')
else:
    warn('로그 파일 없음')

# ────────────────────────────────────────────────────────────
section('최종 요약')
# ────────────────────────────────────────────────────────────
errs  = [m for t,m in REPORT if t=='ERR']
warns = [m for t,m in REPORT if t=='WARN']
total_checks = 9
health = round((1 - len(errs)/max(len(errs)+len(warns)+1,1)) * 100)

print(f'\n  오류: {len(errs)}건 | 경고: {len(warns)}건')
print(f'  진단 시각: {datetime.now().strftime("%Y-%m-%d %H:%M")}')

if errs:
    print('\n  🔴 수정 필요:')
    for m in errs: print(f'    ❌ {m}')
if warns:
    print('\n  🟡 확인 권장:')
    for m in warns: print(f'    ⚠️  {m}')
if not errs and not warns:
    print('\n  🎉 모든 검사 통과! 봇 검증 완료.')
elif not errs:
    print('\n  ✅ 오류 없음. 경고 항목만 확인 후 운영 가능.')
