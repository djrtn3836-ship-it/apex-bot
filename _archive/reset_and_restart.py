"""
페이퍼 트레이딩 DB 초기화 후 새로 시작
- 기존 데이터 백업 후 테이블 초기화
- 오늘부터 14일간 새로 측정
"""
import sqlite3
import shutil
from datetime import datetime
from pathlib import Path

db_path = Path("database/apex_bot.db")
backup_path = Path(f"database/apex_bot_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.db")

# 백업
shutil.copy(db_path, backup_path)
print(f"✅ 기존 데이터 백업: {backup_path}")

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

print("✅ DB 초기화 완료")
print(f"📅 새 측정 시작: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print(f"📅 실거래 판단 목표일: 2026-04-17 (14일 후)")
print()
print("▶ python start_paper.py")
