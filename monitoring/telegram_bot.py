"""APEX BOT –   
  : /////"""
import asyncio
import os
from typing import Optional, Dict
from loguru import logger

try:
    from telegram import Bot, Update
    from telegram.ext import Application, CommandHandler, ContextTypes
    TELEGRAM_OK = True
except ImportError:
    TELEGRAM_OK = False

from config.settings import Settings


class TelegramNotifier:
    EMOJI = {
        "buy":       "🟢",
        "sell":      "🔴",
        "pyramid":   "🔺",
        "stop":      "🛑",
        "trail":     "📌",
        "error":     "❌",
        "info":      "📊",
        "pause":     "⏸",
        "resume":    "▶️",
        "emergency": "🚨",
        "daily":     "📈",
    }

    def __init__(self):
        self.settings     = Settings()
        self._bot: Optional[Bot]         = None
        self._app: Optional[Application] = None
        self._engine_ref                 = None
        self._chat_id                    = os.getenv("TELEGRAM_CHAT_ID", "")
        self._token                      = os.getenv("TELEGRAM_TOKEN", "")
        self._enabled                    = bool(self._token and self._chat_id)
        if not self._enabled:
            logger.warning("   (/ID )")

    # ── 초기화 ──────────────────────────────────────────────────────────
    async def initialize(self, engine_ref=None):
        if not self._enabled or not TELEGRAM_OK:
            return
        self._engine_ref = engine_ref
        try:
            self._app = (
                Application.builder()
                .token(self._token)
                .build()
            )
            self._bot = self._app.bot
            # 명령어 핸들러 등록
            self._app.add_handler(CommandHandler("status",    self._cmd_status))
            self._app.add_handler(CommandHandler("portfolio", self._cmd_portfolio))
            self._app.add_handler(CommandHandler("pause",     self._cmd_pause))
            self._app.add_handler(CommandHandler("resume",    self._cmd_resume))
            self._app.add_handler(CommandHandler("emergency", self._cmd_emergency))
            self._app.add_handler(CommandHandler("help",      self._cmd_help))
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling(drop_pending_updates=True)
            me = await self._bot.get_me()
            logger.info(f"   : @{me.username}")
            # 시작 알림
            await self.send_message(
                f"✅ *APEX BOT 시작*\n"
                f"모드: `PAPER` | 대상: `10개 코인`\n"
                f"/help 로 명령어 확인"
            )
        except Exception as e:
            logger.error(f"  : {e}")
            self._enabled = False

    # ── 기본 전송 ────────────────────────────────────────────────────────
    async def send_message(self, text: str, parse_mode: str = "Markdown"):
        if not self._enabled or not self._bot:
            return
        try:
            await self._bot.send_message(
                chat_id    = self._chat_id,
                text       = text,
                parse_mode = parse_mode,
            )
        except Exception as e:
            logger.warning(f"  : {e}")

    # ── 매수 체결 알림 ───────────────────────────────────────────────────
    async def notify_buy(self, market: str, price: float, amount_krw: float,
                         reason: str = "", strategy: str = ""):
        coin = market.replace("KRW-", "")
        msg = (
            f"{self.EMOJI['buy']} *매수 체결*\n"
            f"┌ 코인: `{coin}`\n"
            f"├ 체결가: `₩{price:,.1f}`\n"
            f"├ 금액: `₩{amount_krw:,.0f}`\n"
            f"├ 전략: `{strategy or reason}`\n"
            f"└ 사유: `{reason}`"
        )
        await self.send_message(msg)

    # ── 매도 체결 알림 ───────────────────────────────────────────────────
    async def notify_sell(self, market: str, price: float, volume: float,
                          profit_rate: float = 0.0, reason: str = ""):
        coin      = market.replace("KRW-", "")
        amount    = price * volume
        emoji     = "🟢" if profit_rate >= 0 else "🔴"
        sign      = "+" if profit_rate >= 0 else ""
        msg = (
            f"{self.EMOJI['sell']} *매도 체결* {emoji}\n"
            f"┌ 코인: `{coin}`\n"
            f"├ 체결가: `₩{price:,.1f}`\n"
            f"├ 금액: `₩{amount:,.0f}`\n"
            f"├ 수익률: `{sign}{profit_rate:.2f}%`\n"
            f"└ 사유: `{reason}`"
        )
        await self.send_message(msg)

    # ── 피라미딩 알림 ────────────────────────────────────────────────────
    async def notify_pyramid(self, market: str, amount_krw: float, step: int = 1):
        coin = market.replace("KRW-", "")
        msg = (
            f"{self.EMOJI['pyramid']} *피라미딩 추가매수*\n"
            f"┌ 코인: `{coin}`\n"
            f"├ 추가금액: `₩{amount_krw:,.0f}`\n"
            f"└ 단계: `{step}차`"
        )
        await self.send_message(msg)

    # ── 트레일링 스탑 활성 알림 ──────────────────────────────────────────
    async def notify_trail_activated(self, market: str, profit_pct: float, trail_price: float):
        coin = market.replace("KRW-", "")
        msg = (
            f"{self.EMOJI['trail']} *트레일링 스탑 활성*\n"
            f"┌ 코인: `{coin}`\n"
            f"├ 현재수익: `+{profit_pct:.2f}%`\n"
            f"└ 트레일가: `₩{trail_price:,.2f}`"
        )
        await self.send_message(msg)

    # ── 리스크 이벤트 알림 ───────────────────────────────────────────────
    async def notify_risk(self, event: str, detail: str):
        msg = (
            f"{self.EMOJI['stop']} *리스크 이벤트*\n"
            f"┌ 유형: `{event}`\n"
            f"└ 내용: `{detail}`"
        )
        await self.send_message(msg)

    # ── 에러 알림 ────────────────────────────────────────────────────────
    async def notify_error(self, error: str, context: str = ""):
        msg = (
            f"{self.EMOJI['error']} *오류 발생*\n"
            f"┌ 위치: `{context}`\n"
            f"└ 내용: `{error[:200]}`"
        )
        await self.send_message(msg)

    # ── 1시간 자동 현황 요약 ─────────────────────────────────────────────
    async def send_hourly_summary(self):
        if not self._enabled:
            return
        try:
            eng = self._engine_ref
            if not eng:
                return
            # 잔고
            try:
                cash = await eng.adapter.get_balance("KRW")
            except Exception:
                cash = 0
            # 포지션
            try:
                positions = eng.portfolio.get_positions()
            except Exception:
                positions = []
            total_pnl = 0.0
            pos_lines = []
            for p in positions:
                market   = getattr(p, "market", "?")
                entry    = getattr(p, "entry_price", 0)
                current  = getattr(p, "current_price", entry)
                invested = getattr(p, "invested_amount", 0)
                pnl_pct  = (current - entry) / entry * 100 if entry > 0 else 0
                pnl_krw  = invested * pnl_pct / 100
                total_pnl += pnl_krw
                e = "🟢" if pnl_pct >= 0 else "🔴"
                pos_lines.append(f"{e} `{market.replace('KRW-','')}` {pnl_pct:+.2f}%")
            regime = getattr(eng, "_market_regime", "UNKNOWN")
            fg     = getattr(eng.fear_greed, "last_value", "N/A") if hasattr(eng, "fear_greed") else "N/A"
            body   = "\n".join(pos_lines) if pos_lines else "포지션 없음"
            msg = (
                f"{self.EMOJI['info']} *1시간 현황 요약*\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"{body}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"💰 현금: `₩{cash:,.0f}`\n"
                f"📊 총손익: `₩{total_pnl:+,.0f}`\n"
                f"🌍 레짐: `{regime}` | 😱 공포탐욕: `{fg}`"
            )
            await self.send_message(msg)
        except Exception as e:
            logger.warning(f"   : {e}")

    # ── 일일 리포트 ──────────────────────────────────────────────────────
    async def send_daily_report(self, stats: Dict):
        total_pnl  = stats.get("total_pnl", 0)
        win_rate   = stats.get("win_rate", 0)
        trades     = stats.get("total_trades", 0)
        total_asset= stats.get("total_asset", 0)
        emoji      = "🟢" if total_pnl >= 0 else "🔴"
        msg = (
            f"{self.EMOJI['daily']} *일일 리포트*\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💰 총자산: `₩{total_asset:,.0f}`\n"
            f"{emoji} 일손익: `₩{total_pnl:+,.0f}`\n"
            f"🎯 승률: `{win_rate:.1f}%`\n"
            f"📝 거래수: `{trades}건`\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Good night! 내일도 화이팅 🌙"
        )
        await self.send_message(msg)

    # ── 긴급 알림 ────────────────────────────────────────────────────────
    async def send_emergency_alert(self, message: str):
        await self.send_message(f"{self.EMOJI['emergency']} *긴급 알림*\n{message}")

    # ── 명령어 핸들러 ────────────────────────────────────────────────────
    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.send_hourly_summary()

    async def _cmd_portfolio(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.send_hourly_summary()

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self._engine_ref:
            await update.message.reply_text(f"{self.EMOJI['pause']} 신규 매수 일시 중단")

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self._engine_ref:
            await update.message.reply_text(f"{self.EMOJI['resume']} 매수 재개")

    async def _cmd_emergency(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            f"{self.EMOJI['emergency']} 긴급 매도 명령 수신\n"
            "/confirm_emergency 로 확정하세요"
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = (
            f"*🤖 APEX BOT 명령어*\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"/status \\- 현재 현황 요약\n"
            f"/portfolio \\- 포지션 수익률\n"
            f"/pause \\- 매수 중단\n"
            f"/resume \\- 매수 재개\n"
            f"/emergency \\- 긴급 전량 매도\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📡 자동알림: 매수/매도/피라미딩/손절/1시간요약"
        )
        await update.message.reply_text(msg, parse_mode="MarkdownV2")

    async def stop(self):
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception:
                pass
