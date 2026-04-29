import sqlite3
db_path = r'C:\Users\hdw38\Desktop\달콩\bot\apex_bot\database\apex_bot.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = cursor.fetchall()
print('=== DB 테이블 목록 ===')
for t in tables:
    print(f'  {t[0]}')

print()
print('=== 각 테이블 스키마 ===')
for t in tables:
    tname = t[0]
    cursor.execute(f'PRAGMA table_info({tname})')
    cols = cursor.fetchall()
    cursor.execute(f'SELECT COUNT(*) FROM {tname}')
    cnt = cursor.fetchone()[0]
    print(f'[{tname}] ({cnt}행)')
    for c in cols:
        print(f'  col{c[0]}: {c[1]} ({c[2]})')
    print()

conn.close()
