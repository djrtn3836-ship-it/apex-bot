#!/usr/bin/env python3
"""
APEX BOT 자동 진단 프로그램
실행: python apex_doctor.py
"""
import sqlite3
import ast
import os
import re
from pathlib import Path
from datetime import datetime, timedelta

RESULT = []

def check(name, status, detail='', fix=''):
    icon = '✅' if status == 'OK' else ('⚠️' if status == 'WARN' else '❌')
    RESULT.append((icon, name, detail, fix))
    print(icon + ' [' + status + '] ' + name + (': ' + detail if detail else ''))

print('=' * 60)
print('APEX BOT 자동 진단 | ' + datetime.now().strftime('%Y-%m-%d %H:%M'))
print('=' * 60)
print('')

# ══════════════════════════════════════════
# 1. 봇 프로세스 확인
# ══════════════════════════════════════════
print('【1】 프로세스 상태')
import subprocess
result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq python.exe'], capture_output=True, text=True)
py_count = result.stdout.count('python.exe')
if py_count >= 1:
    check('봇 프로세스', 'OK', str(py_count) + '개 실행 중')
else:
    check('봇 프로세스', 'ERROR', '실행 중인 python 없음', 'Start-Process python main.py --mode paper')

# ══════════════════════════════════════════
# 2. 로그 파일 최신 확인
# ══════════════════════════════════════════
print('')
print('【2】 로그 상태')
log_path = Path('logs') / ('apex_bot_' + datetime.now().strftime('%Y-%m-%d') + '.log')
if log_path.exists():
    lines_all = log_path.read_text(encoding='utf-8', errors='ignore').splitlines()
    # 최근 2시간 로그만 분석
    cutoff = (datetime.now() - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M')
    lines = [l for l in lines_all if l[:16] >= cutoff] or lines_all[-200:]
    last_lines = lines[-20:] if len(lines) >= 20 else lines
    # 마지막 로그 시간 확인
    last_time = None
    for l in reversed(last_lines):
        m = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', l)
        if m:
            try:
                last_time = datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S')
            except:
                pass
            break
    if last_time:
        diff = (datetime.now() - last_time).total_seconds()
        if diff < 180:
            check('로그 갱신', 'OK', str(int(diff)) + '초 전 업데이트')
        else:
            check('로그 갱신', 'WARN', str(int(diff//60)) + '분 전 업데이트 (봇 멈춤 가능성)', '봇 재시작 필요')
    # 오류 카운트
    errors   = [l for l in lines if 'ERROR' in l]
    warnings = [l for l in lines if 'WARNING' in l and '429' not in l]
    rate_429 = [l for l in lines if '429' in l]
    check('ERROR 수', 'OK' if len(errors)==0 else 'WARN', str(len(errors)) + '건')
    check('WARNING 수 (429 제외)', 'OK' if len(warnings)<=5 else 'WARN', str(len(warnings)) + '건')
    check('API 429 Rate Limit', 'OK' if len(rate_429)<=50 else 'WARN', str(len(rate_429)) + '건 (정상범위 50↓)')
    # 최근 오류 출력
    if errors:
        print('  최근 ERROR:')
        for e in errors[-3:]:
            print('    ' + e[20:100])
else:
    check('로그 파일', 'ERROR', str(log_path) + ' 없음')

# ══════════════════════════════════════════
# 3. DB 거래 통계
# ══════════════════════════════════════════
print('')
print('【3】 거래 성과')
conn = sqlite3.connect('database/apex_bot.db')
cur  = conn.cursor()

cur.execute("SELECT COUNT(*), SUM(CASE WHEN profit_rate>0 THEN 1 ELSE 0 END), ROUND(AVG(profit_rate),4), ROUND(MIN(profit_rate),4), ROUND(MAX(profit_rate),4) FROM trade_history WHERE side='SELL' AND timestamp >= '2026-04-11T14:30:00'")
total, wins, avg, worst, best = cur.fetchone()
wr = round(wins/total*100,1) if total else 0
check('승률', 'OK' if wr>=55 else ('WARN' if wr>=40 else 'ERROR'), str(wr)+'% (목표 55%)')
check('평균 PnL', 'OK' if avg and avg>=0.3 else ('WARN' if avg and avg>=-0.5 else 'ERROR'), str(avg)+'% (목표 +0.3%)')
check('최악 손실', 'OK' if worst and worst>=-3 else 'WARN', str(worst)+'% (목표 -3% 이상)')

# MDD
cur.execute("SELECT profit_rate FROM trade_history WHERE side='SELL' ORDER BY timestamp")
trades = cur.fetchall()
cumul = peak = mdd = 0
for (pnl,) in trades:
    cumul += float(pnl)
    if cumul > peak: peak = cumul
    dd = peak - cumul
    if dd > mdd: mdd = dd
check('MDD', 'OK' if mdd<=5 else ('WARN' if mdd<=15 else 'ERROR'), str(round(mdd,2))+'% (목표 5%↓)')

# profit_rate 이상값 확인
cur.execute("SELECT COUNT(*) FROM trade_history WHERE side='SELL' AND (profit_rate>50 OR profit_rate<-50)")
bad_pnl = cur.fetchone()[0]
check('profit_rate 이상값', 'OK' if bad_pnl==0 else 'ERROR', str(bad_pnl)+'건 (|PnL|>50%)', 'python temp_fix_all.py 실행')

# 오늘 거래
today = datetime.now().strftime('%Y-%m-%d')
cur.execute("SELECT COUNT(*) FROM trade_history WHERE timestamp LIKE '" + today + "%'")
today_count = cur.fetchone()[0]
check('오늘 거래 수', 'OK' if today_count>=3 else 'WARN', str(today_count)+'건 (하루 3건↑ 권장)')

# 미청산 포지션
cur.execute("SELECT COUNT(*) FROM trade_history WHERE side='BUY'")
buys = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM trade_history WHERE side='SELL'")
sells = cur.fetchone()[0]
open_pos = buys - sells
check('미청산 포지션', 'OK' if open_pos<=10 else 'WARN', str(open_pos)+'개')
conn.close()

# ══════════════════════════════════════════
# 4. 코드 문법 검사
# ══════════════════════════════════════════
print('')
print('【4】 코드 문법 검사')
files_to_check = [
    'core/engine.py',
    'core/portfolio_manager.py',
    'models/inference/predictor.py',
    'models/train/auto_trainer.py',
    'data/storage/db_manager.py',
    'config/settings.py',
    'signals/signal_combiner.py',
    'monitoring/dashboard.py',
]
for fp in files_to_check:
    p = Path(fp)
    if p.exists():
        try:
            ast.parse(p.read_text(encoding='utf-8'))
            check(fp, 'OK')
        except SyntaxError as e:
            check(fp, 'ERROR', '라인 ' + str(e.lineno) + ': ' + str(e.msg), fp + ' 수정 필요')
    else:
        check(fp, 'WARN', '파일 없음')

# ══════════════════════════════════════════
# 5. 핵심 설정값 확인
# ══════════════════════════════════════════
print('')
print('【5】 핵심 설정값')
cfg = Path('config/settings.py').read_text(encoding='utf-8')
eng = Path('core/engine.py').read_text(encoding='utf-8')

# ML 임계값
m = re.search(r'buy_signal_threshold.*?=.*?([\d.]+)', cfg)
val = float(m.group(1)) if m else 0
check('ML BUY 임계값', 'OK' if val>=0.60 else 'WARN', str(val) + ' (권장 0.62↑)')

# 쿨다운 실제값 확인
_cd_matches = re.findall(r'< (\d+)', Path('core/engine.py').read_text(encoding='utf-8'))
_cd_matches = re.findall(r'(\d+)', ''.join(ll for ll in Path('core/engine.py').read_text(encoding='utf-8').splitlines() if '< 1200' in ll or 'total_seconds' in ll and '1200' in ll))
cd_val = int(_cd_matches[0]) if _cd_matches else 0
check('쿨다운', 'OK' if cd_val>=1200 else 'WARN', str(cd_val) + '초 (권장 1200↑)')

# 모델 파일
model_path = Path('models/saved/ensemble_best.pt')
if model_path.exists():
    mtime = datetime.fromtimestamp(model_path.stat().st_mtime)
    age_hours = (datetime.now() - mtime).total_seconds() / 3600
    size_mb = round(model_path.stat().st_size/1024/1024, 1)
    check('ML 모델 파일', 'OK' if age_hours<72 else 'WARN',
          str(size_mb)+'MB | 최종수정: '+mtime.strftime('%m-%d %H:%M') + ' (' + str(round(age_hours,1)) + 'h 전)')
else:
    check('ML 모델 파일', 'ERROR', '없음', 'python train_retrain.py 실행')

# AutoTrainer TRAIN_SCRIPT
m3 = re.search(r'TRAIN_SCRIPT\s*=\s*"(.+?)"', Path('models/train/auto_trainer.py').read_text(encoding='utf-8'))
ts = m3.group(1) if m3 else '없음'
check('AutoTrainer 스크립트', 'OK' if ts=='train_retrain.py' else 'WARN', ts)

# ══════════════════════════════════════════
# 6. 최종 요약
# ══════════════════════════════════════════
print('')
print('=' * 60)
ok    = sum(1 for r in RESULT if r[0]=='✅')
warn  = sum(1 for r in RESULT if r[0]=='⚠️')
error = sum(1 for r in RESULT if r[0]=='❌')
total_checks = len(RESULT)
score = round(ok/total_checks*100, 1)

print('진단 완료: ✅' + str(ok) + '  ⚠️' + str(warn) + '  ❌' + str(error) + '  (건강도: ' + str(score) + '%)')
print('')
if error > 0:
    print('🚨 즉시 수정 필요:')
    for r in RESULT:
        if r[0] == '❌':
            print('  - ' + r[1] + ': ' + r[3])
if warn > 0:
    print('⚠️  확인 권장:')
    for r in RESULT:
        if r[0] == '⚠️':
            print('  - ' + r[1] + (': ' + r[3] if r[3] else ''))
print('=' * 60)