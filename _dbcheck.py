import sqlite3
conn = sqlite3.connect('database/apex_bot.db')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print('=== 테이블 목록 ===')
for t in cur.fetchall():
    print(' ', t[0])
    cur2 = conn.cursor()
    cur2.execute('PRAGMA table_info(' + t[0] + ')')
    for c in cur2.fetchall():
        print('    ' + c[1] + ' (' + c[2] + ')')
conn.close()
