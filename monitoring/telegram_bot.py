"""APEX BOT – Telegram Notifier v3.0.0
수정이력:
  - /pause /resume 실제 엔진 동작 구현
  - /emergency 2단계 확인 후 전량매도 구현
  - /confirm_emergency /cancel_emergency 추가
  - send_hourly_summary 포지션 조회 수정 (open_positions)
  - 총자산 이중계산 버그 수정
  - 시작 메시지 LIVE/PAPER 동적 처리
  - /help MarkdownV2 → Markdown 통일
  - pause/resume state 검증 추가
  - 가독성 최적화
"""
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


# [작업3] 레짐 한글 표시 매핑
_REGIME_KR: dict = {
    "TRENDING_UP":   "📈 상승추세",
    "TRENDING_DOWN": "📉 하락추세",
    "RANGING":       "↔️ 횡보",
    "VOLATILE":      "⚡ 변동성",
    "BEAR_REVERSAL": "🔄 반등시도",
    "RECOVERY":      "🌱 회복장",
    "UNKNOWN":       "❓ 불명",
}

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
        "ok":        "✅",
        "warn":      "⚠️",
    }

    def __init__(self):
        self.settings                        = Settings()
        self._bot: Optional[Bot]             = None
        self._app: Optional[Application]     = None
        self._engine_ref                     = None
        self._chat_id                        = os.getenv("TELEGRAM_CHAT_ID", "")
        self._token                          = os.getenv("TELEGRAM_TOKEN", "")
        self._enabled                        = bool(self._token and self._chat_id)
        self._emergency_confirm_pending: bool = False
        if not self._enabled:
            logger.warning("[Telegram] 토큰/CHAT_ID 미설정 → 텔레그램 비활성화")

    # ────────────────────────────────────────────────────────────────────
    # 초기화
    # ────────────────────────────────────────────────────────────────────
    async def initialize(self, engine_ref=None):
        if not self._enabled or not TELEGRAM_OK:
            return
        if self._app is not None:
            await self.stop()
        self._engine_ref = engine_ref
        try:
            self._app = Application.builder().token(self._token).build()
            self._bot = self._app.bot

            # 기존 세션 강제 만료
            try:
                await self._bot.delete_webhook(drop_pending_updates=True)
                logger.info("[Telegram] 기존 세션 초기화 완료")
            except Exception as _we:
                logger.debug(f"[Telegram] delete_webhook 무시: {_we}")

            await asyncio.sleep(2)

            # 명령어 핸들러 등록
            handlers = [
                ("status",            self._cmd_status),
                ("portfolio",         self._cmd_portfolio),
                ("pause",             self._cmd_pause),
                ("resume",            self._cmd_resume),
                ("emergency",         self._cmd_emergency),
                ("confirm_emergency", self._cmd_confirm_emergency),
                ("cancel_emergency",  self._cmd_cancel_emergency),
                ("help",              self._cmd_help),
            ]
            for cmd, handler in handlers:
                self._app.add_handler(CommandHandler(cmd, handler))

            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=["message", "callback_query"]
            )

            me = await self._bot.get_me()
            logger.info(f"[Telegram] 봇 시작: @{me.username}")

            # 시작 메시지 (LIVE/PAPER 동적)
            _mode = getattr(
                getattr(self._engine_ref, "settings", None), "mode", "paper"
            ).upper() if self._engine_ref else "UNKNOWN"

            _trading = getattr(
                getattr(self._engine_ref, "settings", None), "trading", None
            ) if self._engine_ref else None
            _coins = getattr(_trading, "target_coins", [])
            _coin_count = len(_coins) if _coins else 10

            _mode_emoji = "🔴" if _mode == "LIVE" else "📋"
            await self.send_message(
                f"{self.EMOJI['ok']} *APEX BOT v3.0.0 시작*\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"{_mode_emoji} 모드: `{_mode}`\n"
                f"🎯 대상: `{_coin_count}개 코인`\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"📌 /help 로 명령어 확인"
            )
        except Exception as e:
            logger.error(f"[Telegram] 초기화 실패: {e}")
            self._enabled = False

    # ────────────────────────────────────────────────────────────────────
    # 기본 전송
    # ────────────────────────────────────────────────────────────────────
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
            logger.warning(f"[Telegram] 전송 실패: {e}")

    # ────────────────────────────────────────────────────────────────────
    # 매수 체결 알림
    # ────────────────────────────────────────────────────────────────────
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

    # ────────────────────────────────────────────────────────────────────
    # 매도 체결 알림
    # ────────────────────────────────────────────────────────────────────
    async def notify_sell(self, market: str, price: float, volume: float,
                          profit_rate: float = 0.0, reason: str = ""):
        coin   = market.replace("KRW-", "")
        amount = price * volume
        emoji  = "🟢" if profit_rate >= 0 else "🔴"
        sign   = "+" if profit_rate >= 0 else ""
        msg = (
            f"{self.EMOJI['sell']} *매도 체결* {emoji}\n"
            f"┌ 코인: `{coin}`\n"
            f"├ 체결가: `₩{price:,.1f}`\n"
            f"├ 금액: `₩{amount:,.0f}`\n"
            f"├ 수익률: `{sign}{profit_rate:.2f}%`\n"
            f"└ 사유: `{reason}`"
        )
        await self.send_message(msg)

    # ────────────────────────────────────────────────────────────────────
    # 피라미딩 알림
    # ────────────────────────────────────────────────────────────────────
    async def notify_pyramid(self, market: str, amount_krw: float, step: int = 1):
        coin = market.replace("KRW-", "")
        msg = (
            f"{self.EMOJI['pyramid']} *피라미딩 추가매수*\n"
            f"┌ 코인: `{coin}`\n"
            f"├ 추가금액: `₩{amount_krw:,.0f}`\n"
            f"└ 단계: `{step}차`"
        )
        await self.send_message(msg)

    # ────────────────────────────────────────────────────────────────────
    # 트레일링 스탑 활성 알림
    # ────────────────────────────────────────────────────────────────────
    async def notify_trail_activated(self, market: str, profit_pct: float,
                                     trail_price: float):
        coin = market.replace("KRW-", "")
        msg = (
            f"{self.EMOJI['trail']} *트레일링 스탑 활성*\n"
            f"┌ 코인: `{coin}`\n"
            f"├ 현재수익: `+{profit_pct:.2f}%`\n"
            f"└ 트레일가: `₩{trail_price:,.2f}`"
        )
        await self.send_message(msg)

    # ────────────────────────────────────────────────────────────────────
    # 리스크 이벤트 알림
    # ────────────────────────────────────────────────────────────────────
    async def notify_risk(self, event: str, detail: str):
        msg = (
            f"{self.EMOJI['stop']} *리스크 이벤트*\n"
            f"┌ 유형: `{event}`\n"
            f"└ 내용: `{detail}`"
        )
        await self.send_message(msg)

    # ────────────────────────────────────────────────────────────────────
    # 에러 알림
    # ────────────────────────────────────────────────────────────────────
    async def notify_error(self, error: str, context: str = ""):
        msg = (
            f"{self.EMOJI['error']} *오류 발생*\n"
            f"┌ 위치: `{context}`\n"
            f"└ 내용: `{error[:200]}`"
        )
        await self.send_message(msg)

    # ────────────────────────────────────────────────────────────────────
    # 1시간 현황 요약 (수동 /status 또는 자동 1h)
    # ────────────────────────────────────────────────────────────────────
    async def send_status_summary(self):
        """[T-1a] /status — 시장상태 + 포지션 요약 (간결)"""
        """1시간 자동 요약 — send_status_summary 위임"""
        await self.send_status_summary()

    async def _send_hourly_summary_legacy(self):
        """레거시 보관용 (직접 호출 금지)"""
        if not self._enabled:
            return
        try:
            eng = self._engine_ref
            if not eng:
                await self.send_message(f"{self.EMOJI['error']} 엔진 참조 없음")
                return

            # KRW 잔고
            try:
                cash = float(await eng.adapter.get_balance("KRW") or 0)
            except Exception:
                cash = getattr(eng, "_cached_krw", 0.0)

            open_pos      = getattr(eng.portfolio, "open_positions", {})
            market_prices = getattr(eng, "_market_prices", {})

            def _fp(p: float) -> str:
                if not p or p <= 0: return "-"
                if p < 10:    return f"₩{p:,.3f}"
                if p < 100:   return f"₩{p:,.2f}"
                if p < 1_000: return f"₩{p:,.1f}"
                return        f"₩{p:,.0f}"

            pos_lines  = []
            coin_value = 0.0
            total_inv  = 0.0

            for market, pos in open_pos.items():
                entry  = float(getattr(pos, "entry_price", 0) or 0)
                volume = float(getattr(pos, "volume",      0) or 0)
                amt    = float(getattr(pos, "amount_krw",  0) or 0)
                sl     = float(getattr(pos, "stop_loss",   0) or 0)
                tp     = float(getattr(pos, "take_profit", 0) or 0)

                _cached = market_prices.get(market)
                if _cached:
                    current = float(_cached)
                else:
                    try:
                        import pyupbit
                        _m    = market
                        _loop = asyncio.get_running_loop()
                        _tick = await _loop.run_in_executor(
                            None, lambda: pyupbit.get_current_price(_m)
                        )
                        current = float(_tick) if _tick else float(entry)
                    except Exception:
                        current = float(entry)

                cur_val    = current * volume
                pnl_krw    = (current - entry) * volume
                pnl_pct    = (current - entry) / entry * 100 if entry > 0 else 0.0
                coin_value += cur_val
                total_inv  += amt

                e    = "🟢" if pnl_pct >= 0 else "🔴"
                coin = market.replace("KRW-", "")
                pos_lines.append(
                    f"{e} `{coin}` {pnl_pct:+.2f}% (`{_fp(pnl_krw)}`)"
                )

            unrealized_pnl = coin_value - total_inv
            total_assets   = cash + coin_value
            _upct = (unrealized_pnl / total_inv * 100) if total_inv > 0 else 0.0

            # 레짐 한글 표시
            _raw_regime = getattr(eng, "_global_regime", None)
            _regime_str = (
                _raw_regime.value
                if hasattr(_raw_regime, "value")
                else str(_raw_regime or "UNKNOWN")
            )
            _regime_kr = _REGIME_KR.get(_regime_str, _regime_str)
            fg    = getattr(eng.fear_greed, "index", "N/A") if hasattr(eng, "fear_greed") else "N/A"
            state = str(getattr(
                getattr(eng, "state_machine", None), "_state",
                type("", (), {"value": "UNKNOWN"})()
            ).value)

            body = "\n".join(pos_lines) if pos_lines else "📭 보유 포지션 없음"

            msg = (
                f"{self.EMOJI['info']} *현황 요약*\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"{body}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"💰 현금: `{_fp(cash)}`\n"
                f"📦 투자중: `{_fp(total_inv)}` ({len(open_pos)}개)\n"
                f"📊 미실현: `₩{unrealized_pnl:+,.0f} ({_upct:+.2f}%)`\n"
                f"💎 총자산: `{_fp(total_assets)}`\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🌍 레짐: `{_regime_kr}` | 😱 공포탐욕: `{fg}`\n"
                f"🤖 상태: `{state}`\n"
                f"📋 상세 포지션: /portfolio"
            )
            await self.send_message(msg)
        except Exception as e:
            logger.warning(f"[Telegram] status_summary 오류: {e}")

    async def send_portfolio_detail(self):
        """[T-1b] /portfolio — 포지션 상세 (진입시각·보유시간·전략명)"""
        if not self._enabled:
            return
        try:
            eng = self._engine_ref
            if not eng:
                await self.send_message(f"{self.EMOJI['error']} 엔진 참조 없음")
                return

            open_pos      = getattr(eng.portfolio, "open_positions", {})
            market_prices = getattr(eng, "_market_prices", {})

            if not open_pos:
                await self.send_message("📭 보유 포지션이 없습니다.")
                return

            def _fp(p: float) -> str:
                if not p or p <= 0: return "-"
                if p < 10:    return f"₩{p:,.3f}"
                if p < 100:   return f"₩{p:,.2f}"
                if p < 1_000: return f"₩{p:,.1f}"
                return        f"₩{p:,.0f}"

            from datetime import datetime as _dt
            _now = _dt.now()
            pos_blocks = []

            for market, pos in open_pos.items():
                entry    = float(getattr(pos, "entry_price", 0) or 0)
                volume   = float(getattr(pos, "volume",      0) or 0)
                amt      = float(getattr(pos, "amount_krw",  0) or 0)
                sl       = float(getattr(pos, "stop_loss",   0) or 0)
                tp       = float(getattr(pos, "take_profit", 0) or 0)
                strategy = str(getattr(pos, "strategy", "-") or "-")
                entry_time = getattr(pos, "entry_time", None)

                # 보유시간 계산
                if entry_time:
                    try:
                        if isinstance(entry_time, str):
                            from datetime import datetime as _dtp
                            entry_time = _dtp.fromisoformat(entry_time)
                        hold_sec  = (_now - entry_time).total_seconds()
                        hold_h    = int(hold_sec // 3600)
                        hold_m    = int((hold_sec % 3600) // 60)
                        hold_str  = f"{hold_h}h {hold_m}m"
                        entry_str = entry_time.strftime("%m/%d %H:%M")
                    except Exception:
                        hold_str  = "-"
                        entry_str = "-"
                else:
                    hold_str  = "-"
                    entry_str = "-"

                # 현재가
                _cached = market_prices.get(market)
                if _cached:
                    current = float(_cached)
                else:
                    try:
                        import pyupbit
                        _m    = market
                        _loop = asyncio.get_running_loop()
                        _tick = await _loop.run_in_executor(
                            None, lambda: pyupbit.get_current_price(_m)
                        )
                        current = float(_tick) if _tick else float(entry)
                    except Exception:
                        current = float(entry)

                pnl_krw = (current - entry) * volume
                pnl_pct = (current - entry) / entry * 100 if entry > 0 else 0.0
                e       = "🟢" if pnl_pct >= 0 else "🔴"
                coin    = market.replace("KRW-", "")

                # SL까지 거리 (%)
                sl_dist = (current - sl) / current * 100 if current > 0 and sl > 0 else 0.0
                sl_warn = " ⚠️" if sl_dist < 2.0 else ""

                pos_blocks.append(
                    f"{e} *{coin}* {pnl_pct:+.2f}%\n"
                    f"  진입: `{_fp(entry)}` ({entry_str})\n"
                    f"  현재: `{_fp(current)}` | 보유: `{hold_str}`\n"
                    f"  손익: `₩{pnl_krw:+,.0f}`\n"
                    f"  SL: `{_fp(sl)}`{sl_warn} | TP: `{_fp(tp)}`\n"
                    f"  전략: `{strategy}`"
                )

            msg = (
                f"📦 *포지션 상세* ({len(open_pos)}개)\n"
                f"━━━━━━━━━━━━━━━━\n"
                + "\n─────────────────\n".join(pos_blocks) +
                f"\n━━━━━━━━━━━━━━━━\n"
                f"⚠️ SL 2% 미만 경고 표시"
            )
            await self.send_message(msg)
        except Exception as e:
            logger.warning(f"[Telegram] portfolio_detail 오류: {e}")

    async def send_hourly_summary(self):  # 스케줄러 호환 유지 → send_status_summary 위임
        if not self._enabled:
            return
        try:
            eng = self._engine_ref
            if not eng:
                await self.send_message(f"{self.EMOJI['error']} 엔진 참조 없음")
                return

            # KRW 잔고
            try:
                cash = float(await eng.adapter.get_balance("KRW") or 0)
            except Exception:
                cash = getattr(eng, "_cached_krw", 0.0)

            # 포지션
            open_pos      = getattr(eng.portfolio, "open_positions", {})
            market_prices = getattr(eng, "_market_prices", {})

            # 가격대별 소수점 포맷 헬퍼
            def _fp(p: float) -> str:
                if not p or p <= 0: return "-"
                if p < 10:          return f"₩{p:,.3f}"
                if p < 100:         return f"₩{p:,.2f}"
                if p < 1_000:       return f"₩{p:,.1f}"
                return              f"₩{p:,.0f}"

            pos_lines  = []
            coin_value = 0.0
            total_inv  = 0.0

            for market, pos in open_pos.items():
                entry  = float(getattr(pos, "entry_price", 0) or 0)
                volume = float(getattr(pos, "volume",      0) or 0)
                amt    = float(getattr(pos, "amount_krw",  0) or 0)
                sl     = float(getattr(pos, "stop_loss",   0) or 0)
                tp     = float(getattr(pos, "take_profit", 0) or 0)

                # 현재가: 캐시 우선 → Upbit 실시간 조회 → 진입가 fallback
                _cached = market_prices.get(market)
                if _cached:
                    current = float(_cached)
                else:
                    try:
                        import pyupbit
                        _m    = market
                        _loop = asyncio.get_running_loop()
                        _tick = await _loop.run_in_executor(
                            None, lambda: pyupbit.get_current_price(_m)
                        )
                        current = float(_tick) if _tick else float(entry)
                    except Exception:
                        current = float(entry)

                cur_val    = current * volume
                pnl_krw    = (current - entry) * volume
                pnl_pct    = (current - entry) / entry * 100 if entry > 0 else 0.0
                coin_value += cur_val
                total_inv  += amt

                e    = "🟢" if pnl_pct >= 0 else "🔴"
                coin = market.replace("KRW-", "")

                pos_lines.append(
                    f"{e} `{coin}` {pnl_pct:+.2f}% (₩{pnl_krw:+,.0f})\n"
                    f"   진입{_fp(entry)} → 현재{_fp(current)}\n"
                    f"   SL:{_fp(sl)} / TP:{_fp(tp)}"
                )

            # 미실현 손익 (ZeroDivision 방지)
            unrealized_pnl = coin_value - total_inv
            total_assets   = cash + coin_value
            _upct = (unrealized_pnl / total_inv * 100) if total_inv > 0 else 0.0

            # 시장/봇 상태
            # [H-3 FIX] _global_regime(GlobalRegime enum) → .value 문자열
            _raw_regime = getattr(eng, "_global_regime", None)
            regime = (
                _raw_regime.value
                if hasattr(_raw_regime, "value")
                else str(_raw_regime or "UNKNOWN")
            )
            fg     = getattr(eng.fear_greed, "index", "N/A") if hasattr(eng, "fear_greed") else "N/A"
            state  = str(getattr(
                getattr(eng, "state_machine", None), "_state",
                type("", (), {"value": "UNKNOWN"})()
            ).value)

            body = "\n\n".join(pos_lines) if pos_lines else "📭 보유 포지션 없음"

            msg = (
                f"{self.EMOJI['info']} *현황 요약*\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"{body}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"💰 현금: `₩{cash:,.0f}`\n"
                f"📦 투자중: `₩{total_inv:,.0f}` ({len(open_pos)}개)\n"
                f"📊 미실현: `₩{unrealized_pnl:+,.0f} ({_upct:+.2f}%)`\n"
                f"💎 총자산: `₩{total_assets:,.0f}`\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🌍 레짐: `{regime}` | 😱 공포탐욕: `{fg}`\n"
                f"🤖 상태: `{state}`"
            )
            await self.send_message(msg)
        except Exception as e:
            logger.warning(f"[Telegram] hourly_summary 오류: {e}")

    async def send_daily_report(self, stats: Dict):
        total_pnl   = stats.get("total_pnl",   0)
        win_rate    = stats.get("win_rate",     0)
        trades      = stats.get("total_trades", 0)
        total_asset = stats.get("total_asset",  0)
        emoji       = "🟢" if total_pnl >= 0 else "🔴"
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

    # ────────────────────────────────────────────────────────────────────
    # 긴급 알림
    # ────────────────────────────────────────────────────────────────────
    async def send_emergency_alert(self, message: str):
        await self.send_message(
            f"{self.EMOJI['emergency']} *긴급 알림*\n{message}"
        )

    # ════════════════════════════════════════════════════════════════════
    # 명령어 핸들러
    # ════════════════════════════════════════════════════════════════════

    async def _cmd_status(self, update: Update,
                          context: ContextTypes.DEFAULT_TYPE):
        """[T-1a] /status — 시장상태 + 포지션 요약"""
        await self.send_status_summary()

    async def _cmd_portfolio(self, update: Update,
                             context: ContextTypes.DEFAULT_TYPE):
        """[T-1b] /portfolio — 포지션 상세"""
        await self.send_portfolio_detail()

    # ── /pause ──────────────────────────────────────────────────────────
    async def _cmd_pause(self, update: Update,
                         context: ContextTypes.DEFAULT_TYPE):
        if not self._engine_ref:
            await update.message.reply_text("❌ 엔진 참조 없음")
            return
        from core.state_machine import BotState
        current = getattr(
            getattr(self._engine_ref, "state_machine", None), "_state", None
        )
        # [FIX] 이미 PAUSED 상태 확인
        if current == BotState.PAUSED:
            await update.message.reply_text(
                f"{self.EMOJI['warn']} 이미 일시중단 상태입니다.\n재개: /resume"
            )
            return
        # [FIX] RUNNING 상태가 아니면 transition 불가
        if current != BotState.RUNNING:
            await update.message.reply_text(
                f"{self.EMOJI['warn']} 현재 상태 `{getattr(current,'value','?')}`에서는 중단 불가"
            )
            return
        try:
            self._engine_ref.pause(from_telegram=True)
            logger.info("[Telegram] /pause 실행됨")
            await update.message.reply_text(
                f"{self.EMOJI['pause']} *신규 매수 중단됨*\n"
                f"기존 포지션 관리는 계속됩니다.\n"
                f"재개하려면: /resume",
                parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ pause 실패: {e}")

    # ── /resume ─────────────────────────────────────────────────────────
    async def _cmd_resume(self, update: Update,
                          context: ContextTypes.DEFAULT_TYPE):
        if not self._engine_ref:
            await update.message.reply_text("❌ 엔진 참조 없음")
            return
        from core.state_machine import BotState
        current = getattr(
            getattr(self._engine_ref, "state_machine", None), "_state", None
        )
        # [FIX] 이미 RUNNING 상태 확인
        if current == BotState.RUNNING:
            await update.message.reply_text(
                f"{self.EMOJI['warn']} 이미 실행 중입니다."
            )
            return
        try:
            self._engine_ref.resume(from_telegram=True)
            logger.info("[Telegram] /resume 실행됨")
            await update.message.reply_text(
                f"{self.EMOJI['resume']} *매수 재개됨*\n"
                f"정상 운영으로 복귀합니다.",
                parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ resume 실패: {e}")

    # ── /emergency (1단계: 확인 요청) ───────────────────────────────────
    async def _cmd_emergency(self, update: Update,
                             context: ContextTypes.DEFAULT_TYPE):
        if not self._engine_ref:
            await update.message.reply_text("❌ 엔진 참조 없음")
            return
        open_pos = getattr(
            getattr(self._engine_ref, "portfolio", None),
            "open_positions", {}
        )
        if not open_pos:
            await update.message.reply_text("📭 청산할 포지션이 없습니다.")
            return
        self._emergency_confirm_pending = True
        coins = "\n".join(
            f"  • {m.replace('KRW-','')}"
            for m in open_pos.keys()
        )
        await update.message.reply_text(
            f"{self.EMOJI['emergency']} *긴급 전량매도 요청*\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"아래 {len(open_pos)}개 포지션 시장가 매도:\n"
            f"{coins}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"✅ 확정: /confirm\\_emergency\n"
            f"❌ 취소: /cancel\\_emergency",
            parse_mode="Markdown"
        )

    # ── /confirm_emergency (2단계: 실제 전량매도) ────────────────────────
    async def _cmd_confirm_emergency(self, update: Update,
                                     context: ContextTypes.DEFAULT_TYPE):
        if not self._emergency_confirm_pending:
            await update.message.reply_text(
                f"{self.EMOJI['warn']} 먼저 /emergency 를 입력하세요."
            )
            return
        self._emergency_confirm_pending = False
        if not self._engine_ref:
            await update.message.reply_text("❌ 엔진 참조 없음")
            return
        try:
            eng      = self._engine_ref
            open_pos = list(
                getattr(eng.portfolio, "open_positions", {}).keys()
            )
            if not open_pos:
                await update.message.reply_text("📭 청산할 포지션이 없습니다.")
                return

            await update.message.reply_text(
                f"{self.EMOJI['emergency']} *긴급 전량매도 시작*\n"
                f"총 {len(open_pos)}개 포지션 처리 중...",
                parse_mode="Markdown"
            )

            # [FIX] 신규 매수 먼저 차단
            from core.state_machine import BotState
            current = getattr(
                getattr(eng, "state_machine", None), "_state", None
            )
            if current == BotState.RUNNING:
                eng.pause()

            # [FIX] 순차 매도 (동시 실행 시 _selling_markets 충돌 방지)
            results = []
            for market in open_pos:
                try:
                    await eng._execute_sell(market, "긴급전량매도_텔레그램")
                    coin = market.replace("KRW-", "")
                    results.append(f"✅ {coin}")
                    logger.info(f"[EMERGENCY] {market} 매도 완료")
                except Exception as _se:
                    coin = market.replace("KRW-", "")
                    results.append(f"❌ {coin}: {str(_se)[:30]}")
                    logger.error(f"[EMERGENCY] {market} 매도 실패: {_se}")

            result_msg = "\n".join(results)
            await update.message.reply_text(
                f"🏁 *긴급매도 완료*\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"{result_msg}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"재개하려면: /resume",
                parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ 긴급매도 오류: {e}")
            logger.error(f"[EMERGENCY] 전량매도 오류: {e}")

    # ── /cancel_emergency ───────────────────────────────────────────────
    async def _cmd_cancel_emergency(self, update: Update,
                                    context: ContextTypes.DEFAULT_TYPE):
        self._emergency_confirm_pending = False
        await update.message.reply_text(
            f"{self.EMOJI['ok']} 긴급매도 취소됨. 정상 운영 중입니다."
        )

    # ── /help ────────────────────────────────────────────────────────────
    async def _cmd_help(self, update: Update,
                        context: ContextTypes.DEFAULT_TYPE):
        """[작업3] /help — 한글 도움말 보강"""
        msg = (
            f"*🤖 APEX BOT v3.0.0 명령어 안내*\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📊 /status\n"
            f"   시장 레짐 + 포지션 요약 + 총자산\n\n"
            f"📦 /portfolio\n"
            f"   포지션 상세 (진입가·현재가·SL/TP\n"
            f"   진입시각·보유시간·전략명 포함)\n\n"
            f"⏸ /pause\n"
            f"   신규 매수 중단 (기존 포지션 유지)\n\n"
            f"▶️ /resume\n"
            f"   신규 매수 재개\n\n"
            f"🚨 /emergency\n"
            f"   긴급 전량매도 요청 (2단계 확인)\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📡 *자동 알림 항목*\n"
            f"  • 매수/매도 체결\n"
            f"  • 피라미딩 추가매수\n"
            f"  • 트레일링 스탑 활성\n"
            f"  • 1시간 단위 현황 요약\n"
            f"  • 일일 성과 리포트"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    # ────────────────────────────────────────────────────────────────────
    # 종료
    # ────────────────────────────────────────────────────────────────────
    async def stop(self):
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception as _e:
                logger.debug(f"[Telegram] stop 오류 (무시): {_e}")
            finally:
                self._app = None
