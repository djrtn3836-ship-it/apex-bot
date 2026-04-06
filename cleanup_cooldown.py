import sqlite3
conn = sqlite3.connect('database/apex_bot.db')
cur = conn.cursor()
cur.execute("DELETE FROM bot_state WHERE key LIKE 'sl_cooldown_%'")
conn.commit()
deleted = cur.rowcount
print(f'테스트 쿨다운 {deleted}개 삭제 완료')
cur.execute("SELECT COUNT(*) FROM bot_state")
print(f'bot_state 잔여: {cur.fetchone()[0]}개')
conn.close()
