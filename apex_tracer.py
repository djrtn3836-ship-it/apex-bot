from pathlib import Path
import re, sqlite3, subprocess, sys
from datetime import datetime

sys.path.insert(0, '.')
errors = []
warnings = []
oks = []

def err(msg): errors.append(msg); print(f'  ❌ {msg}')
def warn(msg): warnings.append(msg); print(f'  ⚠️  {msg}')
def ok(msg): oks.append(msg); print(f'  ✅ {msg}')

engine = Path('core/engine.py').read_text(encoding='utf-8').splitlines()

print('='*60)
print('APEX BOT 정밀 전수검사 v3')
print(f'검사 시각: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
print('='*60)

print('\n【1】 가격 업데이트 흐름')
if any('_held_markets' in l and '_missing' in l for l in engine):
    ok('보유포지션 가격업데이트 보장 로직 존재')
else:
    err('보유포지션 가격업데이트 누락 → SL 미작동 위험')

update_cnt = sum(1 for l in engine if 'self._market_prices[market] = p' in l or 'self._market_prices[market] = price' in l)
if update_cnt >= 2: ok(f'_market_prices 업데이트 {update_cnt}곳 (REST+WS)')
else: warn(f'_market_prices 업데이트 {update_cnt}곳')

print('\n【2】 SL/TP 흐름')
atr_cap = [i+1 for i,l in enumerate(engine) if '* 0.97' in l and not l.strip().startswith('#')]
if atr_cap: warn(f'ATR SL cap 0.97(-3%) 활성 line {atr_cap} (안전장치로 정상)')
else: ok('ATR SL cap 0.97 없음')

restore_bad = [i+1 for i,l in enumerate(engine[2980:3110])
    if '* 0.97' in l and 'stop_loss' in l and not engine[2980+i].strip().startswith('#')]
restore_ok = [i+1 for i,l in enumerate(engine[2980:3110]) if '* 0.985' in l]
if restore_ok and not restore_bad: ok(f'복원 SL -1.5% 정상')
elif restore_bad: err(f'복원 SL -3% 잔존 line {restore_bad}')

print('\n【3】 매수 흐름')
active_buy = [i+1 for i,l in enumerate(engine)
    if 'await self._execute_buy' in l and not l.strip().startswith('#')]
if len(active_buy) == 1: ok(f'_execute_buy 활성 1곳 line {active_buy[0]}')
elif len(active_buy) == 0: err('_execute_buy 호출 없음')
else: warn(f'_execute_buy 활성 {len(active_buy)}곳 {active_buy}')

low_ml = [(i+1,l.strip()) for i,l in enumerate(engine)
    if re.search(r'ml_score\s*[><=!]+\s*0\.(0[1-9]|[1-5]\d)\b', l)
    and not l.strip().startswith('#')]
if low_ml:
    for ln, code in low_ml: err(f'낮은 ML 임계값 line {ln}: {code[:60]}')
else: ok('ML 임계값 0.62 통일')

fgi_adj = [(i+1,l.strip()) for i,l in enumerate(engine)
    if 'fg_threshold_adj' in l and 'buy_threshold' in l and not l.strip().startswith('#')]
if fgi_adj:
    for ln, code in fgi_adj: err(f'FGI 임계값 하향 활성 line {ln}')
else: ok('FGI 임계값 하향 비활성')

cd_vals = [re.search(r'<\s*(\d+)', l).group(1) for l in engine
    if '_cd_elapsed' in l and re.search(r'<\s*(\d+)', l) and not l.strip().startswith('#')]
cd_set = set(cd_vals)
if cd_set == {'1200'}: ok('쿨다운 1200초 통일')
elif len(cd_set) > 1: err(f'쿨다운 불일치: {cd_set}')

print('\n【4】 매도 흐름')
float_et = any('fromtimestamp' in l and '_pos_et' in l for l in engine)
str_et = any('fromisoformat' in l and '_pos_et' in l for l in engine)
if float_et and str_et: ok('entry_time float+str 모두 처리')
else: err(f'entry_time 처리 불완전')

if any('_held_min < 30' in l for l in engine): ok('30분 보유 조건 존재')
else: err('30분 보유 조건 없음')

print('\n【5】 DB 무결성')
try:
    conn = sqlite3.connect('database/apex_bot.db')
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM trade_history WHERE ABS(profit_rate) > 50 AND side='SELL'")
    if cur.fetchone()[0] > 0: err('profit_rate 50% 초과 존재')
    else: ok('profit_rate 범위 정상')
    cur.execute("""SELECT COUNT(*) FROM trade_history b
        LEFT JOIN trade_history s ON b.market=s.market AND s.side='SELL' AND s.timestamp>b.timestamp
        WHERE b.side='BUY' AND s.id IS NULL""")
    open_p = cur.fetchone()[0]
    ok(f'미청산 포지션: {open_p}개')
    cur.execute("""SELECT COUNT(*), ROUND(AVG(profit_rate),4), ROUND(MIN(profit_rate),4)
        FROM trade_history WHERE side='SELL' AND timestamp >= '2026-04-11T14:30:00'
        AND timestamp < '2026-04-13T20:20:00'""")
    r = cur.fetchone()
    ok(f'버그손절 제외 성과: {r[0]}건 | 평균PnL: {r[1]}% | 최악: {r[2]}%')
    conn.close()
except Exception as e: err(f'DB 오류: {e}')

print('\n【6】 모델 파일')
mp = Path('models/saved/ensemble_best.pt')
if mp.exists():
    age_h = (datetime.now()-datetime.fromtimestamp(mp.stat().st_mtime)).total_seconds()/3600
    if age_h > 72: warn(f'모델 {age_h:.0f}시간 전 훈련')
    else: ok(f'모델 최신 ({age_h:.1f}시간 전)')
else: err('모델 파일 없음')

print('\n【7】 봇 프로세스 및 로그')
result = subprocess.run(['tasklist','/FI','IMAGENAME eq python.exe'], capture_output=True, text=True)
procs = result.stdout.count('python.exe')
if procs >= 1: ok(f'봇 프로세스 {procs}개 실행 중')
else: err('봇 프로세스 없음')

log_files = sorted(Path('logs').glob('*.log'), key=lambda f: f.stat().st_mtime, reverse=True)
if log_files:
    age_sec = (datetime.now()-datetime.fromtimestamp(log_files[0].stat().st_mtime)).total_seconds()
    if age_sec < 120: ok(f'로그 갱신 {age_sec:.0f}초 전')
    else: err(f'로그 갱신 {age_sec:.0f}초 전 → 봇 멈춤 의심')
    recent = log_files[0].read_text(encoding='utf-8', errors='ignore').splitlines()[-300:]
    errs_log = [l for l in recent if '| ERROR' in l]
    warns_log = [l for l in recent if '| WARNING' in l and '429' not in l and 'orphan' not in l.lower() and 'BOT 보유' not in l]
    circuit = [l for l in recent if ('서킷' in l or 'circuit' in l.lower()) and ('L4' in l or 'L3' in l or 'CRITICAL' in l)]
    if errs_log: err(f'최근 ERROR {len(errs_log)}건')
    else: ok('최근 ERROR 0건')
    if warns_log: warn(f'최근 WARNING {len(warns_log)}건: {warns_log[-1][25:90]}')
    else: ok('최근 WARNING 0건')
    if circuit: err(f'서킷브레이커 감지: {circuit[-1][25:90]}')
    else: ok('서킷브레이커 미발동')

print('\n【8】 설정값 검증')
try:
    from config.settings import get_settings
    s = get_settings()
    if s.risk.buy_signal_threshold == 0.62: ok(f'ML BUY 임계값 0.62')
    else: err(f'ML BUY 임계값 {s.risk.buy_signal_threshold} (0.62 아님)')
    if s.risk.sell_signal_threshold == 0.55: ok(f'ML SELL 임계값 0.55')
    else: warn(f'ML SELL 임계값 {s.risk.sell_signal_threshold}')
    if s.risk.consecutive_loss_limit >= 3: ok(f'연속손실한도 {s.risk.consecutive_loss_limit}회')
    else: warn(f'연속손실한도 {s.risk.consecutive_loss_limit}회 (너무 낮음)')
    if s.risk.daily_loss_limit <= 0.10: ok(f'일일손실한도 {s.risk.daily_loss_limit}')
    else: warn(f'일일손실한도 {s.risk.daily_loss_limit}')
    # max_positions는 engine에서 직접 확인
    max_pos = [(i+1,l.strip()) for i,l in enumerate(engine)
        if 'max_positions' in l and '=' in l and 'self.' in l and 'def ' not in l]
    if max_pos: ok(f'max_positions 설정: {max_pos[0][1][:50]}')
    else: warn('max_positions 설정 위치 확인 불가')
except Exception as e: warn(f'설정 로드 오류: {e}')

print('\n' + '='*60)
print(f'최종: 오류 {len(errors)}건 | 경고 {len(warnings)}건 | 정상 {len(oks)}건')
if errors:
    print('\n🔴 즉시 수정 필요:')
    for e in errors: print(f'  → {e}')
if warnings:
    print('\n🟡 확인 권장:')
    for w in warnings: print(f'  → {w}')
if not errors and not warnings:
    print('✅ 모든 항목 이상 없음')
print('='*60)
