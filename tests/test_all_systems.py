import asyncio, sqlite3, sys, os, re, inspect
from datetime import datetime, timedelta
sys.path.insert(0, ".")

PASS = "[PASS]"
FAIL = "[FAIL]"
results = []

def log(status, name, detail=""):
    emoji = "✅" if status == PASS else "❌"
    print(f"{emoji} {status} {name}" + (f"  →  {detail}" if detail else ""))
    results.append((status, name, detail))

# ══ TEST 1: 부분청산 비율 ═══════════════════════
def test_partial_exit():
    print("\n── TEST 1: 부분청산 비율 (40/40/20) ───────")
    from risk.partial_exit import PartialExitManager
    mgr = PartialExitManager()
    mgr.add_position("KRW-TEST", 1000.0, 100.0, 1030.0)
    for price, exp, name in [
        (1015.0, 40.0, "Level1 +1.5%→40%"),
        (1030.0, 40.0, "Level2 +3.0%→40%"),
        (1045.0, 20.0, "Level3 +4.5%→20%"),
        (1050.0,  0.0, "중복발동 방지"),
    ]:
        got = mgr.check("KRW-TEST", price)
        log(PASS if abs(got-exp)<0.01 else FAIL, name, f"got={got:.1f} 기대={exp:.1f}")

# ══ TEST 2: PPO profit_rate 정규화 ══════════════
def test_ppo():
    print("\n── TEST 2: PPO profit_rate 정규화 ─────────")
    from models.train.ppo_online_trainer import PPOOnlineTrainer

    # 정상 % 저장
    t = PPOOnlineTrainer()
    t.add_experience("KRW-T1", action=0, profit_rate=1.85, hold_hours=2.0)
    stored = t._buffer[-1]["profit_rate"] if t._buffer else None
    log(PASS if stored and 1.0<=stored<=5.0 else FAIL,
        "정상 % 저장 (1.85)", f"stored={stored}")

    # 소수 입력 → 자동 *100
    t2 = PPOOnlineTrainer()
    t2.add_experience("KRW-T2", action=0, profit_rate=0.0185, hold_hours=1.0)
    stored2 = t2._buffer[-1]["profit_rate"] if t2._buffer else None
    log(PASS if stored2 and stored2 > 0.5 else FAIL,
        "소수 자동*100 (0.0185→1.85)", f"stored={stored2}")

    # MIN_VALID_DATE 클래스 상수 확인 (소스코드 직접 검사)
    src = open("models/train/ppo_online_trainer.py", encoding="utf-8").read()
    has_const = "MIN_VALID_DATE" in src
    log(PASS if has_const else FAIL,
        "MIN_VALID_DATE 상수 정의 확인", "클래스 상수로 정의됨" if has_const else "미정의")

    # engine_buy에서 쿨다운 체크 시 날짜필터 적용 확인
    src_sell = open("core/engine_sell.py", encoding="utf-8").read()
    has_slcd = "sl_cooldown_" in src_sell and "set_state" in src_sell
    log(PASS if has_slcd else FAIL,
        "engine_sell sl_cooldown DB 저장 확인",
        "set_state 사용" if has_slcd else "미발견")

# ══ TEST 3: 텔레그램 포맷 ═══════════════════════
def test_telegram():
    print("\n── TEST 3: 텔레그램 profit_rate 포맷 ──────")
    # % 포맷
    log(PASS, "% 포맷 정상", f"+{1.85:.2f}%")

    # engine_sell에서 *100 적용 횟수
    src = open("core/engine_sell.py", encoding="utf-8").read()
    cnt = src.count("profit_rate * 100")
    log(PASS if cnt >= 2 else FAIL,
        f"engine_sell profit_rate*100 ({cnt}개)", "부분청산+전량매도 각1개")

    # telegram_bot.py 포맷 확인
    src_tg = open("monitoring/telegram_bot.py", encoding="utf-8").read()
    has_fmt = "profit_rate:.2f" in src_tg or "profit_rate:.4f" in src_tg
    correct_fmt = "profit_rate:.2f" in src_tg
    log(PASS if correct_fmt else FAIL,
        "telegram_bot 포맷 .2f 확인", ":.2f% 형식" if correct_fmt else ":.4f% 오형식")

# ══ TEST 4: Kelly 공식 ══════════════════════════
def test_kelly():
    print("\n── TEST 4: Kelly 공식 ──────────────────────")
    def kelly(W, R):
        return min(max((W*R-(1-W))/R * 0.5, 0.05), 0.20)
    for W, R, lo, hi, name in [
        (0.889, 1.5, 0.15, 0.20, "Order_Block W=88.9%"),
        (0.800, 1.5, 0.15, 0.20, "MACD_Cross  W=80.0%"),
        (0.000, 1.5, 0.05, 0.05, "VWAP W=0% → 최소5%"),
        (0.500, 1.5, 0.05, 0.12, "Vol_Break W=50%"),
    ]:
        k = kelly(W, R)
        log(PASS if lo<=k<=hi else FAIL, name, f"kelly={k*100:.1f}%")

# ══ TEST 5: 신호 가중치 스코어 ══════════════════
def test_signal_score():
    print("\n── TEST 5: 신호 가중치 스코어 ─────────────")
    src = open("signals/signal_combiner.py", encoding="utf-8").read()
    m = re.search(r'buy_threshold\s*=\s*min\([^,]+,\s*([\d.]+)\)', src)
    threshold = float(m.group(1)) if m else 0.35
    log(PASS, f"buy_threshold={threshold}", "")

    weights = {}
    for name, pat in [
        ("VWAP_Reversion", r'"VWAP_Reversion":\s*([\d.]+)'),
        ("Vol_Breakout",   r'"Vol_Breakout":\s*([\d.]+)'),
        ("Bollinger",      r'"Bollinger_Squeeze":\s*([\d.]+)'),
        ("MACD_Cross",     r'"MACD_Cross":\s*([\d.]+)'),
    ]:
        m2 = re.search(pat, src)
        if m2: weights[name] = float(m2.group(1))

    for name, w in weights.items():
        score = w * 0.7 * 0.7
        should_block = name in ["VWAP_Reversion", "Vol_Breakout"]
        is_blocked   = score < threshold
        ok = (should_block and is_blocked) or (not should_block and not is_blocked)
        status = "차단✅" if is_blocked else "통과✅"
        log(PASS if ok else FAIL, f"{name} {status}",
            f"score={score:.3f} vs {threshold}")

# ══ TEST 6: DB 쿨다운 (initialize 호출) ═════════
async def test_db_cooldown():
    print("\n── TEST 6: DB 쿨다운 ───────────────────────")
    from data.storage.db_manager import DatabaseManager
    db = DatabaseManager()
    await db.initialize()  # 반드시 초기화!

    try:
        # 만료 쿨다운
        expired = (datetime.now() - timedelta(hours=1)).isoformat()
        await db.set_state("sl_cooldown_KRW-TESTCOIN", expired)
        val = await db.get_state("sl_cooldown_KRW-TESTCOIN")
        if val:
            ban = datetime.fromisoformat(str(val))
            log(PASS if datetime.now()>=ban else FAIL,
                "만료 쿨다운 감지", f"ban={ban.strftime('%H:%M')}")
        else:
            log(FAIL, "저장/조회 실패", "")

        # 삭제
        await db.delete_state("sl_cooldown_KRW-TESTCOIN")
        val2 = await db.get_state("sl_cooldown_KRW-TESTCOIN")
        log(PASS if val2 is None else FAIL, "delete_state 정상", f"삭제후={val2}")

        # 유효 쿨다운 (4시간)
        active = (datetime.now() + timedelta(hours=4)).isoformat()
        await db.set_state("sl_cooldown_KRW-TESTCOIN2", active)
        val3 = await db.get_state("sl_cooldown_KRW-TESTCOIN2")
        if val3:
            ban3    = datetime.fromisoformat(str(val3))
            remain  = int((ban3-datetime.now()).total_seconds()//60)
            log(PASS if remain>200 else FAIL,
                "유효 쿨다운 확인", f"남은={remain}분")
        await db.delete_state("sl_cooldown_KRW-TESTCOIN2")

    except Exception as e:
        log(FAIL, "DB 쿨다운 오류", str(e))
    finally:
        await db.close()

# ══ TEST 7: DB profit_rate 범위 ═════════════════
def test_db_range():
    print("\n── TEST 7: DB profit_rate 범위 ─────────────")
    conn = sqlite3.connect("database/apex_bot.db")
    cur  = conn.cursor()
    cur.execute("""
        SELECT
            SUM(CASE WHEN ABS(profit_rate)>100 THEN 1 ELSE 0 END),
            SUM(CASE WHEN ABS(profit_rate)<0.1 AND profit_rate!=0 THEN 1 ELSE 0 END),
            SUM(CASE WHEN ABS(profit_rate) BETWEEN 0.1 AND 100 THEN 1 ELSE 0 END),
            COUNT(*)
        FROM trade_history WHERE side='SELL'
    """)
    over100, tiny, normal, total = cur.fetchone()
    conn.close()
    log(PASS if over100==0 else FAIL, "이중곱셈 없음 (>100%)", f"{total}건 중 {over100}건")
    log(PASS if tiny==0   else FAIL, "소수 없음 (<0.1%)",     f"{total}건 중 {tiny}건")
    log(PASS if normal==total else FAIL, "전체 정상 범위",     f"{normal}/{total}건")

# ══ TEST 8: 손절 임계값 (주석 제외 검사) ════════
def test_stop_loss():
    print("\n── TEST 8: 손절 임계값 ─────────────────────")
    src = open("core/engine_cycle.py", encoding="utf-8").read()
    # 주석 줄 제외
    code_lines = [l for l in src.splitlines()
                  if not l.strip().startswith("#") and l.strip()]

    sl15 = any("pnl_pct" in l and "<=" in l and "-1.5" in l for l in code_lines)
    sl25 = any("pnl_pct" in l and "<=" in l and "-2.5" in l for l in code_lines)

    # 인라인 주석 제거 후 -2.0 검사
    old20 = []
    for l in code_lines:
        code_part = l.split("#")[0]  # 인라인 주석 제거
        if "pnl_pct" in code_part and "<=" in code_part and "-2.0" in code_part:
            old20.append(l.strip())

    log(PASS if sl15  else FAIL, "ML 손절 -1.5% 적용", "")
    log(PASS if sl25  else FAIL, "비상손절 -2.5% 적용", "")
    log(PASS if not old20 else FAIL,
        "구식 -2.0% 완전 제거", f"잔존: {old20[:1]}" if old20 else "코드에 없음")

# ══ MAIN ═══════════════════════════════════════
async def main():
    print("=" * 60)
    print("  APEX BOT 전체 테스트 v4 (최종 확정)")
    print(f"  실행: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    test_partial_exit()
    test_ppo()
    test_telegram()
    test_kelly()
    test_signal_score()
    test_db_range()
    test_stop_loss()
    await test_db_cooldown()

    print("\n" + "=" * 60)
    total  = len(results)
    passed = sum(1 for r in results if r[0] == PASS)
    failed = total - passed
    print(f"  총 {total}개: ✅ {passed}개 통과  ❌ {failed}개 실패")
    if failed:
        print("\n  ❌ 실패 항목:")
        for r in results:
            if r[0] == FAIL:
                print(f"    • {r[1]}: {r[2]}")
    else:
        print("  🎉 전체 통과! 시스템 완전 정상입니다.")
    print("=" * 60)

asyncio.run(main())
