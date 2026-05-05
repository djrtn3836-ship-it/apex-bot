# apex_verify.py — APEX BOT 통합 검증 v1.1
# -*- coding: utf-8 -*-
"""
APEX BOT 통합 검증 스크립트 v1.1
변경: EB-7/EB-1 검증 파일 engine.py → engine_buy.py 수정
     전역 부유 SQL 주석 제거
--live  : 업비트 실잔고 교차검증 포함
--full  : 로그 전체 항목 출력 포함
"""
import argparse
import ast
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# ── 경로 설정 ────────────────────────────────────────────────
BASE    = Path(__file__).parent
DB      = BASE / "database" / "apex_bot.db"
LOG_DIR = BASE / "logs"
TODAY   = datetime.now().strftime("%Y-%m-%d")

# ── 설정 로드 ────────────────────────────────────────────────
try:
    sys.path.insert(0, str(BASE))
    from config.settings import get_settings
    _s = get_settings()
    INITIAL_CAPITAL = getattr(
        getattr(_s, "trading", None), "initial_capital", 60_000
    )
    MIN_ORDER_KRW = getattr(
        getattr(_s, "trading", None), "min_order_amount", 5_000
    )
except Exception:
    INITIAL_CAPITAL = 60_000
    MIN_ORDER_KRW   = 5_000

# ── 출력 헬퍼 ────────────────────────────────────────────────
RESULTS: list[tuple[str, str, str]] = []

def ok(name, detail=""):
    RESULTS.append(("✅", name, detail))
    print(f"  ✅ {name}" + (f" — {detail}" if detail else ""))

def warn(name, detail=""):
    RESULTS.append(("⚠️ ", name, detail))
    print(f"  ⚠️  {name}" + (f" — {detail}" if detail else ""))

def err(name, detail=""):
    RESULTS.append(("❌", name, detail))
    print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))

def section(title: str):
    print(f"\n{'='*62}")
    print(f"  {title}")
    print(f"{'='*62}")

# ════════════════════════════════════════════════════════════
# 【1】 프로세스 및 로그 상태
# ════════════════════════════════════════════════════════════
def check_process_and_log(full: bool):
    section("【1】 프로세스 및 로그 상태")

    r = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq python.exe"],
        capture_output=True, text=True
    )
    cnt = r.stdout.count("python.exe")
    if cnt >= 1: ok(f"봇 프로세스 {cnt}개 실행 중")
    else:        err("봇 프로세스 없음", "python main.py --mode live 실행 필요")

    log_files = sorted(LOG_DIR.glob("apex_bot_*.log"),
                       key=lambda f: f.stat().st_mtime, reverse=True)
    if not log_files:
        err("로그 파일 없음"); return

    latest = log_files[0]
    age_s  = (datetime.now() -
               datetime.fromtimestamp(latest.stat().st_mtime)).total_seconds()
    if   age_s < 120:  ok(f"로그 갱신 {age_s:.0f}초 전", latest.name)
    elif age_s < 300: warn(f"로그 갱신 {age_s:.0f}초 전 — 응답 지연 가능")
    else:              err(f"로그 갱신 {age_s/60:.1f}분 전 — 봇 중단 의심")

    lines  = latest.read_text(encoding="utf-8", errors="ignore").splitlines()
    recent = lines[-500:]

    errs    = [l for l in recent if "| ERROR"   in l]
    warns   = [l for l in recent if "| WARNING" in l
               and "429" not in l and "orphan" not in l.lower()
               and "BOT 보유" not in l]
    rate429 = [l for l in recent if "429" in l]
    circuit = [l for l in recent if ("서킷" in l or "circuit" in l.lower())
               and ("L4" in l or "CRITICAL" in l)]

    if errs:    err(f"최근 ERROR {len(errs)}건",  errs[-1][25:90])
    else:       ok("최근 ERROR 0건")
    if warns:   warn(f"최근 WARNING {len(warns)}건", warns[-1][25:80])
    else:       ok("최근 WARNING 0건")
    if rate429: warn(f"API 429 {len(rate429)}건 (50건↓ 정상)")
    else:       ok("API 429 없음")
    if circuit: err("서킷브레이커 감지", circuit[-1][25:80])
    else:       ok("서킷브레이커 미발동")

    vwap_residual = [l for l in recent if "VWAP_Reversion" in l
                     and "제거" not in l and "skip" not in l.lower()]
    if vwap_residual:
        warn(f"VWAP_Reversion 로그 잔존 {len(vwap_residual)}건 — 패치 확인 필요")
    else:
        ok("VWAP_Reversion 완전 제거 확인")

    micro_orders = [l for l in recent
                    if "836" in l and ("BUY" in l or "매수" in l)]
    if micro_orders:
        warn(f"₩836 소액 주문 로그 감지 {len(micro_orders)}건 — EB-1 패치 재확인")
    else:
        ok("EB-1 소액 주문 재발 없음")

    if full:
        print("\n  [최근 ERROR 전체]")
        for l in errs:  print(f"    {l[20:120]}")
        print("\n  [최근 WARNING 전체]")
        for l in warns: print(f"    {l[20:100]}")

# ════════════════════════════════════════════════════════════
# 【2】 코드 문법 및 설정값 검증
# ════════════════════════════════════════════════════════════
def check_code_and_settings():
    section("【2】 코드 문법 및 설정값 검증")

    CHECK_FILES = [
        "core/engine.py",
        "core/engine_buy.py",
        "core/engine_sell.py",
        "core/engine_cycle.py",
        "core/smart_wallet.py",
        "risk/position_sizer.py",
        "strategies/v2/ensemble_engine.py",
        "signals/signal_combiner.py",
        "signals/mtf_signal_merger.py",
        "data/storage/db_manager.py",
        "data/collectors/ws_collector.py",
        "config/settings.py",
    ]
    for fp in CHECK_FILES:
        p = BASE / fp
        if not p.exists():
            warn(fp, "파일 없음"); continue
        try:
            ast.parse(p.read_text(encoding="utf-8"))
            ok(fp)
        except SyntaxError as e:
            err(fp, f"SyntaxError L{e.lineno}: {e.msg}")

    # 설정값 검증
    try:
        s  = get_settings()
        bt = getattr(getattr(s, "risk", None), "buy_signal_threshold",   None)
        st = getattr(getattr(s, "risk", None), "sell_signal_threshold",  None)
        cl = getattr(getattr(s, "risk", None), "consecutive_loss_limit", None)
        dl = getattr(getattr(s, "risk", None), "daily_loss_limit",       None)

        if bt and bt >= 0.60:  ok(f"ML BUY 임계값  {bt}")
        elif bt:               warn(f"ML BUY 임계값  {bt} (권장 0.62↑)")
        if st and st >= 0.50:  ok(f"ML SELL 임계값 {st}")
        elif st:               warn(f"ML SELL 임계값 {st}")
        if cl and cl >= 3:     ok(f"연속손실 한도  {cl}회")
        elif cl:               warn(f"연속손실 한도  {cl}회 (권장 3↑)")
        if dl and dl <= 0.10:  ok(f"일일손실 한도  {dl}")
        elif dl:               warn(f"일일손실 한도  {dl}")
    except Exception as e:
        warn(f"설정 로드 오류: {e}")

    # ── EB-7 / EB-1 검증: engine_buy.py 기준 ──────────────────
    # v1.0 오류: engine.py 를 참조 → 항상 경고 발생
    # v1.1 수정: engine_buy.py 를 참조 (실제 패치 위치)
    buy_path = BASE / "core/engine_buy.py"
    if buy_path.exists():
        buy_code = buy_path.read_text(encoding="utf-8")

        # EB-7: news_boost 부호 — +가 맞음 (부정=음수이므로 +로 빼는 효과)
        if "combined.score = combined.score + news_boost" in buy_code:
            ok("EB-7 news_boost 부호 수정 확인")
        else:
            warn("EB-7 news_boost 부호 확인 필요",
                 "engine_buy.py 에서 'combined.score + news_boost' 검색")

        # EB-1: 최소 주문 가드 — _min_krw_eb1 변수명으로 확인
        if "MIN_ORDER_KRW" in buy_code and "_min_krw_eb1" in buy_code:
            ok("EB-1 소액 주문 가드 확인")
        else:
            warn("EB-1 소액 주문 가드 확인 필요",
                 "engine_buy.py 에서 '_min_krw_eb1' 검색")

        # PATCH-1 적용 확인: _analyze_market_inner 존재 여부
        if "_analyze_market_inner" in buy_code:
            ok("PATCH-1 이중매수 Lock 적용 확인")
        else:
            warn("PATCH-1 이중매수 Lock 미적용",
                 "fix_apex_final_v3.py 재실행 필요")
    else:
        warn("core/engine_buy.py 파일 없음")

    # ── PATCH-2 적용 확인: upbit_adapter.py ──────────────────
    adapter_path = BASE / "execution/upbit_adapter.py"
    if adapter_path.exists():
        adp_code = adapter_path.read_text(encoding="utf-8")
        if "PATCH-2: 실잔고 기반 수량 교정" in adp_code:
            ok("PATCH-2 실잔고 교정 적용 확인")
        else:
            warn("PATCH-2 실잔고 교정 미적용")
    else:
        warn("execution/upbit_adapter.py 파일 없음")

    # ── VWAP 완전 제거 확인: engine.py ───────────────────────
    engine_path = BASE / "core/engine.py"
    if engine_path.exists():
        eng = engine_path.read_text(encoding="utf-8")
        vwap_active = re.findall(
            r'(?<!#).*VWAP_Reversion.*(?:execute|signal|buy|weight)',
            eng
        )
        if vwap_active:
            err(f"VWAP_Reversion 활성 참조 {len(vwap_active)}건 잔존")
        else:
            ok("VWAP_Reversion 코드 레벨 제거 확인")

    # 모델 파일
    mp = BASE / "models/saved/ensemble_best.pt"
    if mp.exists():
        age_h   = (datetime.now() -
                   datetime.fromtimestamp(mp.stat().st_mtime)).total_seconds() / 3600
        size_mb = mp.stat().st_size / 1024 / 1024
        if   age_h < 24:  ok(f"모델 최신 ({age_h:.1f}h 전, {size_mb:.1f}MB)")
        elif age_h < 72: warn(f"모델 {age_h:.0f}h 전 훈련 — 재훈련 권장")
        else:             err(f"모델 {age_h:.0f}h 전 훈련 — 즉시 재훈련 필요")
    else:
        err("모델 파일 없음", "python train_retrain.py 실행")

# ════════════════════════════════════════════════════════════
# 【3】 DB 무결성 및 포지션 현황
# ════════════════════════════════════════════════════════════
def check_db():
    section("【3】 DB 무결성 및 포지션 현황")

    if not DB.exists():
        err(f"DB 없음: {DB}"); return

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    cur  = conn.cursor()

    cur.execute("""
        SELECT COUNT(*) FROM trade_history
        WHERE side='SELL' AND ABS(profit_rate) > 50
    """)
    bad = cur.fetchone()[0]
    if bad: err(f"profit_rate |>50%| 이상값 {bad}건")
    else:   ok("profit_rate 범위 정상")

    # ── 미청산 체크: timestamp 기준 NOT EXISTS (오탐 수정) ────
    cur.execute("""
        SELECT b.market, b.price, b.amount_krw, b.strategy, b.timestamp,
               ROUND((JULIANDAY('now','localtime') -
                      JULIANDAY(b.timestamp)) * 24, 1) AS held_h
        FROM trade_history b
        WHERE b.side = 'BUY'
          AND NOT EXISTS (
              SELECT 1 FROM trade_history s
              WHERE s.market  = b.market
                AND s.side    = 'SELL'
                AND s.timestamp > b.timestamp
          )
        GROUP BY b.market
        ORDER BY held_h DESC
    """)
    positions = cur.fetchall()

    print(f"\n  열린 포지션: {len(positions)}개")
    print(f"  {'코인':<15} {'진입가':>12} {'금액':>10} "
          f"{'전략':<22} {'보유(h)'}")
    print(f"  {'-'*72}")
    for p in positions:
        flag = "⚠️ " if p["held_h"] and p["held_h"] > 48 else "   "
        print(f"  {flag}{p['market']:<13} "
              f"{p['price']:>12,.1f} "
              f"{p['amount_krw']:>9,.0f}원  "
              f"{str(p['strategy']):<22} "
              f"{p['held_h']:.1f}h")

    if len(positions) <= 5: ok(f"포지션 슬롯 {len(positions)}/5 정상")
    else:                    err(f"포지션 {len(positions)}개 — 슬롯 초과")

    # positions 테이블 vs trade_history 불일치
    try:
        cur.execute("SELECT COUNT(*) FROM positions")
        pos_tbl_cnt = cur.fetchone()[0]
        if pos_tbl_cnt != len(positions):
            warn(f"positions 테이블({pos_tbl_cnt}개) ≠ "
                 f"trade_history 미청산({len(positions)}개) — 재시작 권장")
        else:
            ok(f"positions 테이블 일치 ({pos_tbl_cnt}개)")
    except Exception:
        warn("positions 테이블 조회 실패")

    # 오늘 거래 요약
    cur.execute("""
        SELECT COUNT(*),
               SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN profit_rate <= 0 THEN 1 ELSE 0 END),
               ROUND(AVG(CASE WHEN profit_rate > 0 THEN profit_rate END), 3),
               ROUND(AVG(CASE WHEN profit_rate <= 0 THEN profit_rate END), 3),
               ROUND(SUM(profit_rate * amount_krw / 100), 0)
        FROM trade_history
        WHERE side='SELL' AND DATE(timestamp) = ?
    """, (TODAY,))
    r = cur.fetchone()
    total, wins, losses, aw, al, net = (
        r[0] or 0, r[1] or 0, r[2] or 0,
        r[3] or 0, r[4] or 0, r[5] or 0
    )
    wr = wins / total * 100 if total else 0
    rr = abs(aw / al)       if al     else 0
    ev = (wr / 100 * aw) + ((1 - wr / 100) * al) if total else 0

    print(f"\n  오늘 실현 손익 ({TODAY})")
    print(f"  거래 {total}건 | 승 {wins} 패 {losses} | "
          f"승률 {wr:.1f}% | 손익비 {rr:.2f}x | EV {ev:+.3f}%")
    print(f"  순손익: {net:+,.0f}원  "
          f"{'✅ 흑자' if net >= 0 else '🔴 적자'}")

    conn.close()

# ════════════════════════════════════════════════════════════
# 【4】 전략별 누적 성과
# ════════════════════════════════════════════════════════════
def check_strategy_performance():
    section("【4】 전략별 누적 성과 (최근 14일)")

    if not DB.exists(): return
    conn = sqlite3.connect(str(DB))
    cur  = conn.cursor()

    cutoff = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    cur.execute("""
        SELECT strategy,
               COUNT(*)                                           AS cnt,
               SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END)  AS wins,
               ROUND(AVG(profit_rate), 3)                         AS avg_pnl,
               ROUND(SUM(profit_rate * amount_krw / 100), 0)      AS cum_krw,
               ROUND(AVG(CASE WHEN profit_rate > 0
                          THEN profit_rate END), 3)               AS avg_win,
               ROUND(AVG(CASE WHEN profit_rate <= 0
                          THEN profit_rate END), 3)               AS avg_loss
        FROM trade_history
        WHERE side='SELL' AND profit_rate IS NOT NULL
          AND DATE(timestamp) >= ?
        GROUP BY strategy
        ORDER BY cum_krw DESC
    """, (cutoff,))
    rows = cur.fetchall()

    print(f"  {'전략':<25} {'건':>4} {'승률':>6} "
          f"{'평균':>7} {'누적손익':>11} {'EV':>7} {'판정'}")
    print(f"  {'-'*68}")
    for r in rows:
        strat, cnt, wins, avg, cum, aw, al = r
        wr = wins / cnt if cnt else 0
        aw = aw or 0; al = al or 0
        ev = wr * aw + (1 - wr) * al
        icon = "✅" if ev > 0.3 else ("⚠️ " if ev > 0 else "🔴")
        if strat in ("VWAP_Reversion", "VolBreakout", "Vol_Breakout"):
            icon = "🔴 제거됨"
        print(f"  {icon} {str(strat):<23} {cnt:>4}건 "
              f"{wr*100:>5.0f}% {avg:>+6.3f}%  "
              f"{int(cum):>+10,}원  {ev:>+6.3f}%")

    cur.execute("""
        SELECT COUNT(*) FROM trade_history
        WHERE side='SELL'
          AND strategy IN ('VWAP_Reversion','VolBreakout','Vol_Breakout')
          AND DATE(timestamp) >= ?
    """, (TODAY,))
    vwap_today = cur.fetchone()[0]
    if vwap_today:
        err(f"오늘 VWAP/VolBreakout 거래 {vwap_today}건 감지 — 패치 실패")
    else:
        ok("오늘 VWAP/VolBreakout 거래 없음 — 패치 정상")

    conn.close()

# ════════════════════════════════════════════════════════════
# 【5】 MDD 및 연속손실 리스크
# ════════════════════════════════════════════════════════════
def check_risk():
    section("【5】 MDD 및 연속손실 리스크")

    if not DB.exists(): return
    conn = sqlite3.connect(str(DB))
    cur  = conn.cursor()

    cur.execute("""
        SELECT DATE(timestamp),
               ROUND(SUM(profit_rate * amount_krw / 100), 0) AS daily
        FROM trade_history
        WHERE side='SELL' AND DATE(timestamp) >= '2026-04-17'
        GROUP BY DATE(timestamp)
        ORDER BY DATE(timestamp)
    """)
    cum = peak = mdd = 0
    print(f"  {'날짜':<12} {'일손익':>9} {'누적':>10} {'낙폭':>9}")
    print(f"  {'-'*45}")
    for dt, daily in cur.fetchall():
        cum += (daily or 0)
        if cum > peak: peak = cum
        dd = peak - cum
        if dd > mdd: mdd = dd
        state = "🔴" if dd > 0 else "✅"
        print(f"  {state} {dt:<11} {int(daily or 0):>+9,}  "
              f"{int(cum):>+10,}  {int(dd):>9,}")

    mdd_pct = mdd / INITIAL_CAPITAL * 100 if INITIAL_CAPITAL else 0
    print(f"\n  역대 MDD: {int(mdd):,}원 ({mdd_pct:.1f}%)")
    if   mdd_pct <= 5:   ok(f"MDD {mdd_pct:.1f}% — 양호")
    elif mdd_pct <= 15: warn(f"MDD {mdd_pct:.1f}% — 주의")
    else:                err(f"MDD {mdd_pct:.1f}% — 위험 (목표 15%↓)")

    cur.execute("""
        SELECT profit_rate FROM trade_history
        WHERE side='SELL' ORDER BY rowid DESC LIMIT 20
    """)
    streak = 0
    for (pr,) in cur.fetchall():
        if (pr or 0) < 0: streak += 1
        else:              break
    limit = 5
    if   streak == 0:   ok(f"연속 손실 {streak}회 — 정상")
    elif streak < 3:   warn(f"연속 손실 {streak}회 — {limit-streak}회 여유")
    else:               err(f"연속 손실 {streak}회 — 즉시 모니터링")

    print(f"\n  [bot_state 핵심 지표]")
    try:
        cur.execute("""
            SELECT key, value, updated_at FROM bot_state
            WHERE key IN (
                'sell_cooldown','consecutive_loss_count',
                'circuit_breaker_active','walk_forward_last_result'
            )
        """)
        for key, val, upd in cur.fetchall():
            if key == "walk_forward_last_result":
                try:
                    p = json.loads(val)
                    print(f"    walk_forward: updated={upd} | "
                          f"val_acc={p.get('best_val_acc','?')}")
                except Exception:
                    print(f"    {key}: {str(val)[:60]}")
            elif key == "sell_cooldown":
                try:
                    p = json.loads(val)
                    print(f"    sell_cooldown: {len(p)}개 항목 | {upd}")
                except Exception:
                    print(f"    sell_cooldown: {upd}")
            else:
                print(f"    {key}: {str(val)[:60]} | {upd}")
    except Exception as e:
        warn(f"bot_state 조회 실패: {e}")

    conn.close()

# ════════════════════════════════════════════════════════════
# 【6】 Fear & Greed 및 시장 컨텍스트
# ════════════════════════════════════════════════════════════
def check_market_context():
    section("【6】 Fear & Greed 및 시장 컨텍스트")

    try:
        with urllib.request.urlopen(
            "https://api.alternative.me/fng/?limit=1&format=json", timeout=5
        ) as resp:
            item   = json.loads(resp.read())["data"][0]
            fg_idx = int(item["value"])
            fg_lbl = item["value_classification"]

        icon = "🔴" if fg_idx < 25 else ("⚠️ " if fg_idx < 45 else "✅")
        print(f"  {icon} Fear & Greed: {fg_idx} ({fg_lbl})")

        if fg_idx < 25:
            warn("Extreme Fear — 신규 진입 전체 억제 권장")
        elif fg_idx >= 90:
            warn("Extreme Greed — 신규 매수 자동 차단 중 확인")
        else:
            ok(f"F&G 정상 구간 ({fg_idx})")
    except Exception as e:
        warn(f"F&G API 호출 실패: {e}")

# ════════════════════════════════════════════════════════════
# 【7】 실잔고 교차검증 (--live 옵션 시에만)
# ════════════════════════════════════════════════════════════
def check_live_balance():
    section("【7】 업비트 실잔고 ↔ DB 교차검증")

    try:
        import uuid
        import jwt
        import requests as req

        s  = get_settings()
        ak = getattr(getattr(s, "api", None), "access_key", None)
        sk = getattr(getattr(s, "api", None), "secret_key", None)
        if not ak or not sk:
            warn("API 키 없음 — 교차검증 스킵"); return

        payload = {"access_key": ak, "nonce": str(uuid.uuid4())}
        token   = jwt.encode(payload, sk, algorithm="HS256")
        if isinstance(token, bytes): token = token.decode()

        balances = req.get(
            "https://api.upbit.com/v1/accounts",
            headers={"Authorization": f"Bearer {token}"}, timeout=10
        ).json()

        upbit_map = {
            b["currency"]: float(b.get("balance", 0)) + float(b.get("locked", 0))
            for b in balances if b.get("currency") != "KRW"
        }
        krw = next(
            (float(b.get("balance", 0))
             for b in balances if b["currency"] == "KRW"), 0
        )
        print(f"  KRW 잔고: ₩{krw:,.0f}")

        conn = sqlite3.connect(str(DB))
        rows = conn.execute(
            "SELECT market, entry_price, volume FROM positions"
        ).fetchall()
        conn.close()

        all_ok = True
        for mkt, entry, vol in rows:
            coin = mkt.replace("KRW-", "")
            real = upbit_map.get(coin, 0)
            diff = abs(real - vol)
            if diff < 0.01:
                ok(f"{mkt}: DB={vol:.4f} = 실잔고={real:.4f}")
            else:
                err(f"{mkt}: DB={vol:.4f} ≠ 실잔고={real:.4f} (차이={diff:.4f})")
                all_ok = False

        if not rows:
            ok("포지션 없음 (전량 청산 완료)")
        elif all_ok:
            ok("DB-실잔고 완전 일치")

    except ImportError:
        warn("PyJWT 또는 requests 미설치 — pip install PyJWT requests")
    except Exception as e:
        warn(f"실잔고 교차검증 오류: {e}")

# ════════════════════════════════════════════════════════════
# 최종 요약
# ════════════════════════════════════════════════════════════
def print_summary():
    section("최종 진단 요약")
    ok_cnt   = sum(1 for r in RESULTS if r[0] == "✅")
    warn_cnt = sum(1 for r in RESULTS if r[0] == "⚠️ ")
    err_cnt  = sum(1 for r in RESULTS if r[0] == "❌")
    total    = len(RESULTS)
    score    = ok_cnt / total * 100 if total else 0

    print(f"  ✅ {ok_cnt}  ⚠️  {warn_cnt}  ❌ {err_cnt}  "
          f"(건강도 {score:.1f}%)")

    if err_cnt:
        print("\n  🔴 즉시 수정 필요:")
        for icon, name, detail in RESULTS:
            if icon == "❌":
                print(f"    → {name}" + (f": {detail}" if detail else ""))
    if warn_cnt:
        print("\n  🟡 확인 권장:")
        for icon, name, detail in RESULTS:
            if icon == "⚠️ ":
                print(f"    → {name}" + (f": {detail}" if detail else ""))
    if not err_cnt and not warn_cnt:
        print("  🎉 모든 항목 이상 없음")

    print(f"\n{'='*62}")
    print(f"  실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*62}")

# ════════════════════════════════════════════════════════════
# 진입점
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="APEX BOT 통합 검증")
    parser.add_argument("--live", action="store_true",
                        help="업비트 실잔고 교차검증 포함")
    parser.add_argument("--full", action="store_true",
                        help="로그 전체 항목 출력")
    args = parser.parse_args()

    print(f"\n{'='*62}")
    print(f"  APEX BOT 통합 검증 v1.1")
    print(f"  실행: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  DB:   {DB}")
    print(f"{'='*62}")

    check_process_and_log(args.full)
    check_code_and_settings()
    check_db()
    check_strategy_performance()
    check_risk()
    check_market_context()
    if args.live:
        check_live_balance()

    print_summary()
