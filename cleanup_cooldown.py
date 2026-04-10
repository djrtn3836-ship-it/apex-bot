import sqlite3
conn = sqlite3.connect('database/apex_bot.db')
cur = conn.cursor()
cur.execute("DELETE FROM bot_state WHERE key LIKE 'sl_cooldown_%'")
conn.commit()
deleted = cur.rowcount
print(f'  {deleted}  ')
cur.execute("SELECT COUNT(*) FROM bot_state")
print(f'bot_state : {cur.fetchone()[0]}개')
conn.close()
