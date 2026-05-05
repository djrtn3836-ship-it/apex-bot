# test_positions.py
# 실행: python test_positions.py
# 목적: BUY→upsert, SELL→delete, 재시작→entry_time 연속성 자동 검증

import asyncio
import sqlite3
import os
import sys
import time
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

# 설정 및 DB 경로
from config.settings import get_settings
_settings = get_settings()
DB_PATH = str(_settings.database.db_path)

# DBManager import
try:
    from data.storage.db_manager import DatabaseManager as DBManager
except ImportError as e:
    print(f"❌ DBManager import 실패: {e}")
    sys.exit(1)

# ──────────────────────────────────────────────
# 테스트 설정
# ──────────────────────────────────────────────
TEST_MARKET   = "KRW-TEST_UNIT"
TEST_ENTRY    = 1000.0
TEST_VOLUME   = 10.0
TEST_SL       = 983.0
TEST_TP       = 1030.0
TEST_STRATEGY = "TEST_STRATEGY"
TEST_ENTRY_TS = time.time() - 3600  # 1시간 전 진입 시뮬레이션

PASS = "✅ PASS"
FAIL = "❌ FAIL"

results = []

def record(name, ok, detail=""):
    status = PASS if ok else FAIL
    results.append((name, status, detail))
    print(f"  {status}  {name}" + (f"  →  {detail}" if detail else ""))

# ──────────────────────────────────────────────
# 헬퍼: SQLite 직접 조회 (동기)
# ──────────────────────────────────────────────
def db_query(sql, params=()):
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(sql, params).fetchall()
        return rows
    finally:
        con.close()

# ──────────────────────────────────────────────
# 테스트 본문
# ──────────────────────────────────────────────
async def run_tests():
    print("\n" + "="*60)
    print("  APEX BOT — positions 테이블 단위 테스트")
    print(f"  실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  DB 경로: {DB_PATH}")
    print("="*60 + "\n")

    # DBManager 초기화
    db = DBManager()
    await db.initialize()

    # ── 사전 정리 (이전 테스트 잔여 제거) ──────────────
    await db.delete_position(TEST_MARKET)
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM trade_history WHERE market=?", (TEST_MARKET,))
    con.commit()
    con.close()
    print("[사전 정리] 테스트 데이터 초기화 완료\n")

    # ══════════════════════════════════════════
    # TEST 1: upsert_position — BUY 저장
    # ══════════════════════════════════════════
    print("─── TEST 1: BUY 후 upsert_position 저장 ───")
    pos_data = {
        "market":         TEST_MARKET,
        "entry_price":    TEST_ENTRY,
        "volume":         TEST_VOLUME,
        "amount_krw":     TEST_ENTRY * TEST_VOLUME,
        "stop_loss":      TEST_SL,
        "take_profit":    TEST_TP,
        "strategy":       TEST_STRATEGY,
        "entry_time":     TEST_ENTRY_TS,
        "pyramid_count":  0,
        "partial_exited": False,
        "breakeven_set":  False,
        "max_price":      TEST_ENTRY,
    }
    ok = await db.upsert_position(pos_data)
    record("upsert_position 반환값 True", ok)

    rows = db_query("SELECT * FROM positions WHERE market=?", (TEST_MARKET,))
    record("positions 테이블에 행 존재", len(rows) == 1,
           f"행 수={len(rows)}")

    if rows:
        r = rows[0]
        record("entry_price 정확성",  abs(r[1] - TEST_ENTRY) < 0.01, f"저장={r[1]}")
        record("stop_loss 정확성",    abs(r[4] - TEST_SL)    < 0.01, f"저장={r[4]}")
        record("take_profit 정확성",  abs(r[5] - TEST_TP)    < 0.01, f"저장={r[5]}")
        record("entry_time 저장됨",   r[7] is not None and r[7] != 0, f"저장={r[7]}")
        record("partial_exited=0",    r[9] == 0,                      f"저장={r[9]}")

    # ══════════════════════════════════════════
    # TEST 2: upsert_position — partial_exited 업데이트
    # ══════════════════════════════════════════
    print("\n─── TEST 2: partial_exited 업데이트 ───")
    pos_data["partial_exited"] = True
    pos_data["volume"]         = TEST_VOLUME * 0.5
    await db.upsert_position(pos_data)

    rows = db_query(
        "SELECT partial_exited, volume FROM positions WHERE market=?",
        (TEST_MARKET,)
    )
    if rows:
        record("partial_exited=1 업데이트", rows[0][0] == 1,
               f"저장={rows[0][0]}")
        record("volume 50% 업데이트",
               abs(rows[0][1] - TEST_VOLUME * 0.5) < 0.01,
               f"저장={rows[0][1]}")

    # ══════════════════════════════════════════
    # TEST 3: get_all_positions — 재시작 복원 시뮬레이션
    # ══════════════════════════════════════════
    print("\n─── TEST 3: get_all_positions — 재시작 복원 ───")
    all_pos = await db.get_all_positions()
    test_pos = [p for p in all_pos if p["market"] == TEST_MARKET]
    record("get_all_positions에 포함", len(test_pos) == 1,
           f"전체={len(all_pos)}개")

    if test_pos:
        p = test_pos[0]
        record("entry_time 원본 유지",
               abs(p["entry_time"] - TEST_ENTRY_TS) < 1,
               f"원본={TEST_ENTRY_TS:.0f} 복원={p['entry_time']:.0f}")
        record("partial_exited 복원",
               p["partial_exited"] == True,
               f"복원={p['partial_exited']}")
        record("stop_loss 복원",
               abs(p["stop_loss"] - TEST_SL) < 0.01,
               f"복원={p['stop_loss']}")

        hold_hours = (time.time() - p["entry_time"]) / 3600
        record("보유시간 1h 이상 (entry_time 연속성)",
               hold_hours >= 1.0,
               f"보유={hold_hours:.2f}h")

    # ══════════════════════════════════════════
    # TEST 4: delete_position — SELL 후 삭제
    # ══════════════════════════════════════════
    print("\n─── TEST 4: SELL 후 delete_position ───")
    ok = await db.delete_position(TEST_MARKET)
    record("delete_position 반환값 True", ok)

    rows = db_query(
        "SELECT * FROM positions WHERE market=?", (TEST_MARKET,)
    )
    record("positions 테이블에서 행 삭제됨", len(rows) == 0,
           f"잔여 행={len(rows)}")

    # ══════════════════════════════════════════
    # TEST 5: insert_trade BUY entry_time 저장
    # ══════════════════════════════════════════
    print("\n─── TEST 5: insert_trade BUY entry_time 저장 ───")
    trade_data = {
        "timestamp":   datetime.now().isoformat(),
        "market":      TEST_MARKET,
        "side":        "BUY",
        "price":       TEST_ENTRY,
        "volume":      TEST_VOLUME,
        "amount_krw":  TEST_ENTRY * TEST_VOLUME,
        "fee":         0.0,
        "profit_rate": 0.0,
        "strategy":    TEST_STRATEGY,
        "reason":      "TEST_BUY",
        "entry_time":  datetime.fromtimestamp(TEST_ENTRY_TS).isoformat(),
    }
    ok = await db.insert_trade(trade_data)
    record("insert_trade 반환값 True", ok)

    rows = db_query(
        "SELECT entry_time FROM trade_history WHERE market=? AND side='BUY' ORDER BY id DESC LIMIT 1",
        (TEST_MARKET,)
    )
    record("trade_history BUY entry_time 저장됨",
           len(rows) > 0 and rows[0][0] is not None and rows[0][0] != "",
           f"저장={rows[0][0] if rows else 'N/A'}")

    # ── 테스트 데이터 정리 ──────────────────────────
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM trade_history WHERE market=?", (TEST_MARKET,))
    con.commit()
    con.close()

    # ══════════════════════════════════════════
    # 최종 결과
    # ══════════════════════════════════════════
    await db.close()

    print("\n" + "="*60)
    print("  테스트 결과 요약")
    print("="*60)
    total  = len(results)
    passed = sum(1 for _, s, _ in results if s == PASS)
    failed = total - passed

    for name, status, detail in results:
        print(f"  {status}  {name}" + (f"  ({detail})" if detail else ""))

    print(f"\n  총 {total}개  |  통과 {passed}개  |  실패 {failed}개")

    if failed == 0:
        print("\n  🎉 모든 테스트 통과 — positions 영속화 정상 동작")
        print("  ✅ 변수명 통일 작업으로 넘어갈 수 있습니다")
    else:
        print(f"\n  ⚠️  {failed}개 실패 — 위 항목 확인 후 수정 필요")

    print("="*60 + "\n")
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
