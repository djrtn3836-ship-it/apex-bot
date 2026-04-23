import sqlite3, math, asyncio, os
from datetime import datetime, date
from loguru import logger

try:
    import aiohttp
    AIOHTTP_OK = True
except ImportError:
    AIOHTTP_OK = False

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DB_PATH          = "database/apex_bot.db"

async def send_daily_report():
    """매일 23:50 KST 자동 발송 — 일일 성과 리포트"""
    try:
        today = date.today().isoformat()
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        cur = db.cursor()

        # 오늘 거래 집계
        cur.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN side='SELL' AND profit_rate > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN side='SELL' THEN 1 ELSE 0 END) as sells,
                GROUP_CONCAT(CASE WHEN side='SELL' THEN profit_rate END) as pnl_list,
                MAX(CASE WHEN side='SELL' THEN profit_rate END) as best_pnl,
                MIN(CASE WHEN side='SELL' THEN profit_rate END) as worst_pnl,
                GROUP_CONCAT(CASE WHEN side='SELL' AND profit_rate =
                    (SELECT MAX(profit_rate) FROM trade_history
                     WHERE DATE(timestamp)=? AND side='SELL') THEN market END) as best_market,
                GROUP_CONCAT(CASE WHEN side='SELL' AND profit_rate =
                    (SELECT MIN(profit_rate) FROM trade_history
                     WHERE DATE(timestamp)=? AND side='SELL') THEN market END) as worst_market
            FROM trade_history
            WHERE DATE(timestamp) = ?
        """, (today, today, today))
        row = cur.fetchone()

        sells = row['sells'] or 0
        wins  = row['wins'] or 0
        win_rate = (wins / sells * 100) if sells > 0 else 0.0
        pnls = [float(x) for x in (row['pnl_list'] or "").split(',') if x]
        daily_pnl = sum(pnls)

        # Sharpe (당일)
        if len(pnls) >= 2:
            mean_p = sum(pnls) / len(pnls)
            std_p  = math.sqrt(sum((x - mean_p)**2 for x in pnls) / len(pnls))
            sharpe = (mean_p / std_p * math.sqrt(252)) if std_p > 0 else 0.0
        else:
            sharpe = 0.0

        # AutoTrainer 다음 재학습 시간
        import json, pathlib as pl
        tr = pl.Path("models/saved/train_result.json")
        next_retrain = "정보없음"
        if tr.exists():
            try:
                data = json.loads(tr.read_text(encoding='utf-8'))
                from datetime import datetime, timedelta
                last_ts = datetime.fromisoformat(data.get("timestamp","2000-01-01"))
                next_ts = last_ts + timedelta(days=1)
                diff_h  = max(0, (next_ts - datetime.now()).total_seconds() / 3600)
                next_retrain = f"{diff_h:.1f}시간 후" if diff_h > 0 else "대기 중"
            except Exception:
                pass

        # LiveGuard 상태
        import json as _json, pathlib as _pl
        lg_state_path = _pl.Path("database/live_guard_state.json")
        lg_status = "정상"
        if lg_state_path.exists():
            try:
                lg = _json.loads(lg_state_path.read_text(encoding='utf-8'))
                if lg.get("blocked"):
                    lg_status = f"🔴 차단 중 ({lg.get('block_reason','')})"
            except Exception:
                pass

        # 이모지 결정
        pnl_emoji  = "📈" if daily_pnl >= 0 else "📉"
        wr_emoji   = "✅" if win_rate >= 55 else "⚠️"

        msg = f"""
🤖 *ApexBot 일일 리포트*
📅 {today}

{pnl_emoji} *일일 PnL:* {daily_pnl:+.4f} ({daily_pnl*100:+.2f}%)
{wr_emoji} *승률:* {win_rate:.1f}% ({wins}/{sells}건)
📊 *Sharpe:* {sharpe:.3f}
📉 *최고 수익:* {(row['best_pnl'] or 0)*100:+.2f}% ({row['best_market'] or '-'})
📉 *최대 손실:* {(row['worst_pnl'] or 0)*100:+.2f}% ({row['worst_market'] or '-'})
🛡 *LiveGuard:* {lg_status}
🔄 *다음 재학습:* {next_retrain}
""".strip()

        db.close()
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            logger.warning("[DailyReport] Telegram 미설정 — 콘솔 출력만")
            logger.info(msg)
            return

        if not AIOHTTP_OK:
            logger.warning("[DailyReport] aiohttp 없음")
            return

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        async with aiohttp.ClientSession() as sess:
            await sess.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": msg,
                "parse_mode": "Markdown"
            })
        logger.info("[DailyReport] ✅ 텔레그램 전송 완료")

    except Exception as e:
        logger.error(f"[DailyReport] 전송 실패: {e}")