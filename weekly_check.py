import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect('database/apex_bot.db')
cur = conn.cursor()

# 새 설정 적용 시점 (2026-04-11 14:30) 이후만 분석
NEW_SETTING_DATE = '2026-04-11T14:30:00'

cur.execute("""
    SELECT COUNT(*),
           SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END),
           ROUND(AVG(profit_rate),4),
           ROUND(MIN(profit_rate),4),
           ROUND(MAX(profit_rate),4)
    FROM trade_history
    WHERE side='SELL' AND timestamp >= ?
""", (NEW_SETTING_DATE,))
total, wins, avg, worst, best = cur.fetchone()
wins = wins or 0
total = total or 0
wr = round(wins/total*100,1) if total else 0
daily = round(total/max((datetime.now()-datetime.fromisoformat(NEW_SETTING_DATE)).days,1),1)

# 미청산 포지션
cur.execute("""
    SELECT COUNT(DISTINCT market) FROM trade_history
    WHERE side='BUY' AND market NOT IN (
        SELECT DISTINCT market FROM trade_history WHERE side='SELL'
        AND timestamp >= ?
    )
""", (NEW_SETTING_DATE,))
# 간단히 BUY-SELL 차이로 계산
cur.execute("SELECT COUNT(*) FROM trade_history WHERE side='BUY' AND timestamp >= ?", (NEW_SETTING_DATE,))
buys = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM trade_history WHERE side='SELL' AND timestamp >= ?", (NEW_SETTING_DATE,))
sells = cur.fetchone()[0]
open_pos = max(buys - sells, 0)

print('=== APEX BOT 성과 검증 (새 설정 이후) ===')
print('기준: ' + NEW_SETTING_DATE + ' 이후')
print('')
print('[거래 성과]')
print('  거래: ' + str(total) + '건 | 일평균: ' + str(daily) + '건')
print('  승률: ' + str(wr) + '% (목표 55%)')
print('  평균PnL: ' + str(avg) + '% (목표 +0.3%)')
print('  최고: +' + str(best) + '% | 최악: ' + str(worst) + '%')
print('  미청산 포지션: 약 ' + str(open_pos) + '개')
print('')
print('[목표 달성 여부]')
print('  승률 55%:  ' + ('✅' if wr >= 55 else '❌') + ' ' + str(wr) + '%')
print('  평균PnL:   ' + ('✅' if (avg or 0) >= 0.3 else '❌') + ' ' + str(avg) + '%')
print('  최악손실:  ' + ('✅' if (worst or 0) >= -3.0 else '⚠️') + ' ' + str(worst) + '%')
print('')
if wr >= 55 and (avg or 0) >= 0.3:
    print('최종 판정: 실매매 전환 가능 ✅')
elif wr >= 45:
    print('최종 판정: 임계값 소폭 조정 후 재검증 필요 ⚠️')
elif total < 30:
    print('최종 판정: 데이터 부족 (' + str(total) + '건) - 계속 수집 중 ⏳')
else:
    print('최종 판정: ML 재훈련 필요 ❌')

conn.close()
