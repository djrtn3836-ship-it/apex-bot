import sqlite3
conn = sqlite3.connect('database/apex_bot.db')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cur.fetchall()
for t in tables:
    cur.execute("SELECT COUNT(*) FROM " + t[0])
    count = cur.fetchone()[0]
    print("  " + t[0] + ": " + str(count) + "건")
conn.close()
