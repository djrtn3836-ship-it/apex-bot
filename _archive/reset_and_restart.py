"""DB    
-      
-  14"""
import sqlite3
import shutil
from datetime import datetime
from pathlib import Path

db_path = Path("database/apex_bot.db")
backup_path = Path(f"database/apex_bot_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.db")

# 백업
shutil.copy(db_path, backup_path)
print(f"   : {backup_path}")

conn = sqlite3.connect(db_path)
cur = conn.cursor()

# 기존 거래 데이터 초기화 (테이블 구조는 유지)
cur.execute("DELETE FROM trade_history")
cur.execute("DELETE FROM daily_performance")
cur.execute("DELETE FROM signal_log")
cur.execute("DELETE FROM model_metrics")
cur.execute("DELETE FROM sqlite_sequence WHERE name='trade_history'")

conn.commit()
conn.close()

print(" DB  ")
print(f"   : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print(f"   : 2026-04-17 (14 )")
print()
print(" python start_paper.py")
