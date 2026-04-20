"""
core/engine_schedule.py
─────────────────────────────────────────────────────────────
스케줄 작업 / WebSocket / 대시보드 / 유틸 Mixin

포함 메서드:
    _register_schedules           : 스케줄 등록
    _scheduled_position_summary   : 포지션 요약 (정기)
    _scheduled_performance_check  : 성과 점검 (정기)
    _scheduled_price_update       : 가격 업데이트 (정기)
    _scheduled_daily_data         : 일봉 데이터 수집 (정기)
    _scheduled_daily_report       : 일일 리포트 (정기)
    _scheduled_model_retrain      : 모델 재훈련 (정기)
    _scheduled_ppo_online_retrain : PPO 온라인 재훈련 (정기)
    _scheduled_paper_report       : 페이퍼 성과 리포트 (정기)
    _scheduled_kimchi_update      : 김치프리미엄 업데이트 (정기)
    _scheduled_fear_greed_update  : 공포탐욕지수 업데이트 (정기)
    _scheduled_walk_forward       : 워크포워드 최적화 (정기)
    _scheduled_news_update        : 뉴스 업데이트 (정기)
    _ws_reconnect_loop            : WebSocket 재연결 루프
    _update_dashboard_state       : 대시보드 상태 업데이트
    _get_hold_hours               : 보유시간 계산
    _time_based_tp_threshold      : 시간 기반 TP 임계값
─────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import time
import asyncio
from datetime import datetime
from loguru import logger


class EngineScheduleMixin:
    """스케줄 작업, WebSocket 재연결, 대시보드, 유틸 관련 메서드 Mixin"""

    def _register_schedules(self):
        from datetime import datetime, timedelta

        self.scheduler.add_job(
            self._scheduled_price_update,
            "interval", seconds=60, id="price_update",
        )
        self.scheduler.add_job(
            self._scheduled_daily_data,
            "interval", hours=1, id="daily_data",
        )
        self.scheduler.add_job(
            self._scheduled_daily_report,
            "cron", hour=0, minute=0, id="daily_report",
        )
        self.scheduler.add_job(
            self._scheduled_model_retrain,
            "interval",
            hours=self.settings.ml.retrain_interval_hours,
            id="retrain",
        )
        first_run = datetime.now() + timedelta(hours=24)
        self.scheduler.add_job(
            self._scheduled_paper_report,
            "interval", hours=24, id="paper_report",
            next_run_time=first_run,
        )
        self.scheduler.add_job(
            self._scheduled_kimchi_update,
            "interval", hours=6, id="kimchi_update",
        )
        self.scheduler.add_job(
            self._scheduled_fear_greed_update,
            "interval", hours=1, id="fear_greed_update",
        )
        self.scheduler.add_job(
            self._scheduled_walk_forward,
            "cron", day_of_week="mon", hour=2, minute=0,
            id="walk_forward",
        )
        self.scheduler.add_job(
            self._scheduled_news_update,
            "interval", minutes=30, id="news_update",
        )
        self.scheduler.add_job(
            self._scheduled_position_summary,
            "interval", hours=1, id="position_summary",
        )
        self.scheduler.add_job(
            self._scheduled_performance_check,
            "interval", hours=1, id="performance_check",
        )
        from pathlib import Path
        if not Path("config/optimized_params.json").exists():
            self.scheduler.add_job(
                self._scheduled_walk_forward, "date",
                run_date=datetime.now() + timedelta(minutes=30),
                id="walk_forward_initial",
            )
            logger.info(
                " Walk-Forward  : 30   "
                "(config/optimized_params.json )"
            )
        self.scheduler.add_job(
            lambda: __import__(
                "utils.gpu_utils", fromlist=["warmup_keep_alive"]
            ).warmup_keep_alive(),
            "interval", minutes=5, id="cuda_keepalive",
        )
        self.scheduler.add_job(
            self.telegram.send_hourly_summary,
            "interval", hours=1,
            id="hourly_telegram_summary",
            misfire_grace_time=60,
        )
        self.scheduler.add_job(
            self._scheduled_ppo_online_retrain,
            "cron", day_of_week="sun", hour=4, minute=0,
            id="ppo_online_retrain",
        )
        logger.info(
            f"    "
            f"({len(self.scheduler.get_jobs())}개 작업)"
        )


    async def _scheduled_position_summary(self):
        try:
            from monitoring.dashboard import dashboard_state
            positions = list(self.portfolio._positions.values())
            if not positions:
                return
            now   = datetime.now()
            lines = [
                "📊 <b>APEX BOT 포지션 현황</b>",
                f"🕐 {now.strftime('%m/%d %H:%M')} KST\n",
            ]
            total_invested = total_eval = total_pnl_krw = 0.0
            win_count = 0
            for pos in positions:
                market   = getattr(pos, "market",      "?")
                entry    = float(getattr(pos, "entry_price", 0) or 0)
                qty      = float(getattr(pos, "quantity",    0) or 0)
                current  = float(
                    self.cache_manager.get_current_price(market) or entry
                )
                invested = entry * qty
                eval_val = current * qty
                pnl_pct  = (current - entry) / entry * 100 if entry else 0
                pnl_krw  = eval_val - invested
                total_invested += invested
                total_eval     += eval_val
                total_pnl_krw  += pnl_krw
                if pnl_pct >= 0:
                    win_count += 1
                entry_time = getattr(pos, "entry_time", None)
                try:
                    hold_h   = (
                        (now - entry_time).total_seconds() / 3600
                        if entry_time else 0
                    )
                    hold_str = f"{hold_h:.1f}h"
                except Exception:
                    hold_str = "?"
                sl_pct   = float(getattr(pos, "stop_loss_pct",  -3.0) or -3.0)
                tp_pct   = float(getattr(pos, "take_profit_pct", 5.0) or  5.0)
                sl_dist  = sl_pct - pnl_pct
                tp_dist  = tp_pct - pnl_pct
                ml_info  = (
                    dashboard_state.signals
                    .get("ml_predictions", {})
                    .get(market, {})
                )
                ml_sig   = ml_info.get("signal",     "-")
                ml_conf  = float(ml_info.get("confidence", 0))
                ml_icon  = {
                    "BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"
                }.get(ml_sig, "⚪")
                coin     = market.replace("KRW-", "")
                pnl_icon = "🟢" if pnl_pct >= 0 else "🔴"
                lines.append(
                    f"{pnl_icon} <b>{coin}</b>  "
                    f"{pnl_pct:+.2f}% ({pnl_krw:+,.0f}원)"
                )
                lines.append(
                    f"   진입 {entry:,.0f} → 현재 {current:,.0f}  "
                    f"보유 {hold_str}"
                )
                lines.append(
                    f"   SL까지 {sl_dist:+.1f}%  TP까지 {tp_dist:+.1f}%"
                )
                lines.append(
                    f"   ML {ml_icon}{ml_sig}({ml_conf:.0%})  "
                    f"수량 {qty:.4f}\n"
                )
            total_pnl_pct = (
                (total_eval - total_invested) / total_invested * 100
                if total_invested else 0
            )
            cash         = float(getattr(self.portfolio, "cash", 0) or 0)
            total_assets = total_eval + cash
            lines.append("─────────────────────")
            lines.append(f"💰 총 평가금액: <b>{total_assets:,.0f}원</b>")
            lines.append(
                f"📈 포지션 손익: <b>{total_pnl_pct:+.2f}%</b>  "
                f"({total_pnl_krw:+,.0f}원)"
            )
            lines.append(f"💵 현금 잔고:   {cash:,.0f}원")
            lines.append(f"🏆 수익 포지션: {win_count}/{len(positions)}개")
            fg = getattr(self, "_fear_greed_index", None)
            if fg is not None:
                fg_label = (
                    "극단적 공포" if fg < 25 else
                    "공포"       if fg < 45 else
                    "중립"       if fg < 55 else
                    "탐욕"       if fg < 75 else
                    "극단적 탐욕"
                )
                lines.append(f"\n😨 공포탐욕: {fg}  ({fg_label})")
            btc_status = self.correlation_filter.get_btc_status()
            if btc_status.get("trend") == "DOWN":
                lines.append("⚠️ BTC 하락세 감지 - 신규 매수 차단 중")
            news_sig = dashboard_state.signals.get("news_sentiment", {})
            if news_sig.get("overall_sentiment") in ("BEARISH", "VERY_BEARISH"):
                lines.append(
                    f"📰 뉴스 감성: "
                    f"{news_sig.get('overall_sentiment')} ⚠️"
                )
            await self.telegram.send_message("\n".join(lines))
        except Exception as e:
            logger.debug(f"   : {e}")


    async def _scheduled_performance_check(self):
        try:
            trades  = await self.db_manager.get_trades(limit=50)
            if not trades:
                return

            # ✅ FIX: update()는 호환용 pass, get_metrics()로 dict 반환
            await self.performance_tracker.update(trades)
            metrics = self.performance_tracker.get_metrics(days=14)

            sharpe  = metrics.get("sharpe_ratio", 0)
            mdd     = metrics.get("max_drawdown",  0)
            wr      = metrics.get("win_rate",      0)
            pf      = metrics.get("profit_factor", 0)

            score = 0
            if hasattr(self, "live_readiness"):
                try:
                    score = await self.live_readiness.check(self.performance_tracker)
                except Exception:
                    pass

            logger.info(
                f"성과점검: win_rate={wr:.1%} "
                f"sharpe={sharpe:.2f} "
                f"mdd={mdd:.1%} "
                f"profit_factor={pf:.2f} "
                f"live_score={score:.0f}/100"
            )

            # ✅ FIX: 매 시간 성과를 daily_performance DB에 저장
            try:
                from datetime import datetime as _dt_now
                # [FIX] report 변수 제거 -> _pm 직접 사용
                _pm = self.performance_tracker.get_metrics(days=14)
                await self.db_manager.save_daily_performance({
                    "date":           _dt_now.now().strftime("%Y-%m-%d"),
                    "total_assets":   0,
                    "daily_pnl":      0,
                    "open_positions": len(getattr(self, "_positions", {})),
                    "win_rate":       _pm.get("win_rate", 0),
                    "trade_count":    _pm.get("total_trades", 0),
                    "max_drawdown":   _pm.get("max_drawdown", 0),
                    "sharpe_ratio":   _pm.get("sharpe_ratio", 0),
                })
                logger.debug("✅ hourly performance DB 저장 완료")
            except Exception as _dbe:
                logger.debug(f"hourly performance DB 저장 실패: {_dbe}")

            if score >= 70:
                logger.info("LiveReadiness 70점 이상 - Live 전환 가능")
            elif score < 30 and metrics.get("total_trades", 0) > 20:
                await self.telegram.send_alert(
                    "WARNING",
                    f"LiveReadiness 점수 {score:.0f}/100 - 전략 점검 필요\n"
                    f"Sharpe={sharpe:.2f} | MDD={mdd:.1%} | WR={wr:.1%}",
                )
        except Exception as e:
            logger.debug(f"성과점검 오류: {e}")

    async def _scheduled_price_update(self):
        pass  # ws_collector 실시간 처리


    async def _scheduled_daily_data(self):
        for market in self.settings.trading.target_markets:
            try:
                df = await self.rest_collector.get_ohlcv(market, "day", 200)
                if df is not None:
                    await self.candle_processor.process(market, df, "1440")
            except Exception as e:
                logger.error(f"   ({market}): {e}")


    async def _scheduled_daily_report(self):
        stats     = self.portfolio.get_statistics()
        krw       = await self.adapter.get_balance("KRW")
        total     = self.portfolio.get_total_value(krw)
        daily_pnl = self.portfolio.get_daily_pnl(total)
        report    = {
            **stats,
            "date":           now_kst().strftime("%Y-%m-%d"),
            "daily_pnl":      daily_pnl,
            "total_assets":   total,
            "open_positions": self.portfolio.position_count,
        }
        await self.telegram.notify_daily_report(report)
        try:
            await self.db_manager.save_daily_performance({
                "date":           report.get("date"),
                "total_assets":   report.get("total_assets",   0),
                "daily_pnl":      report.get("daily_pnl",      0),
                "open_positions": report.get("open_positions",  0),
                "win_rate":       report.get("win_rate",        0),
                "trade_count":    report.get("trade_count",     0),
            })
            logger.info(" daily_performance DB  ")
        except Exception as _dpe:
            logger.debug(f"daily_performance  : {_dpe}")


    async def _scheduled_model_retrain(self):
        """Phase 5: train_retrain.py v3.0 연동 자동 재학습"""
        logger.info("ML 앙상블 재학습 시작 (train_retrain v3.0)...")
        # 1. 재학습 전 현재 metrics DB 저장
        try:
            await self.db_manager.save_model_metrics({
                "timestamp":  datetime.now().isoformat(),
                "model_name": "ensemble_pre_retrain",
                "val_acc":    getattr(self._ml_predictor, "_last_val_acc",   0.0),
                "train_loss": getattr(self._ml_predictor, "_last_train_loss", 0.0),
                "val_loss":   getattr(self._ml_predictor, "_last_val_loss",  0.0),
                "parameters": 12299965,
            })
        except Exception as _mme:
            logger.debug(f"model_metrics 저장 실패: {_mme}")

        # 2. train_retrain.py v3.0 별도 프로세스로 실행
        try:
            import subprocess, sys, json
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    [sys.executable, "train_retrain.py"],
                    capture_output=True, text=True,
                    cwd=str(Path(__file__).parent.parent),
                    timeout=7200  # 최대 2시간
                )
            )
            if result.returncode == 0:
                logger.info("train_retrain.py 완료 (returncode=0)")
                # 3. train_result.json 읽어서 결과 로깅
                result_path = Path("models/saved/train_result.json")
                if result_path.exists():
                    try:
                        res = json.loads(result_path.read_text(encoding="utf-8"))
                        val_acc   = res.get("val_acc", 0)
                        samples   = res.get("total_samples", 0)
                        forward_n = res.get("forward_n", 8)
                        logger.info(
                            f"[Retrain] 완료 | val_acc={val_acc:.4f} | "
                            f"samples={samples:,} | FORWARD_N={forward_n}"
                        )
                        # 4. 성공 시 모델 핫리로드
                        if val_acc >= 0.42 and self._ml_predictor:
                            await asyncio.get_event_loop().run_in_executor(
                                None, self._ml_predictor.reload_model
                            )
                            logger.info("[Retrain] 모델 핫리로드 완료")
                            await self.db_manager.save_model_metrics({
                                "timestamp":  datetime.now().isoformat(),
                                "model_name": "ensemble_v3",
                                "val_acc":    val_acc,
                                "train_loss": res.get("final_train_loss", 0.0),
                                "val_loss":   res.get("final_val_loss", 0.0),
                                "parameters": 12299965,
                            })
                        else:
                            logger.warning(
                                f"[Retrain] val_acc={val_acc:.4f} < 0.42, 롤백 유지"
                            )
                    except Exception as _je:
                        logger.warning(f"train_result.json 파싱 실패: {_je}")
            else:
                logger.error(
                    f"train_retrain.py 실패 (returncode={result.returncode})\n"
                    f"STDERR: {result.stderr[-500:] if result.stderr else ''}"
                )
        except asyncio.TimeoutError:
            logger.error("[Retrain] 타임아웃 (2시간 초과)")
        except Exception as e:
            logger.error(f"재학습 스케줄 오류: {e}")

    async def _scheduled_ppo_online_retrain(self):
        try:
            if not hasattr(self, "ppo_online_trainer"):
                return
            stats = self.ppo_online_trainer.get_buffer_stats()
            logger.info(
                f"[PPOOnline]    | "
                f"buffer={stats.get('count',0)}개 | "
                f"avg_profit={stats.get('avg_profit',0):.2%} | "
                f"win_rate={stats.get('win_rate',0):.1%}"
            )
            result = await self.ppo_online_trainer.train_if_ready()
            if result:
                await self._init_ppo_agent()
                await self.telegram.send_message(
                    f"🤖 PPO 온라인 재학습 완료\n"
                    f"경험 {stats.get('count',0)}건 학습\n"
                    f"평균 수익률: {stats.get('avg_profit',0):.2%}\n"
                    f"승률: {stats.get('win_rate',0):.1%}"
                )
                logger.info("[PPOOnline]     +  ")
            else:
                logger.info("[PPOOnline]     (   )")
        except Exception as e:
            logger.error(f"[PPOOnline]  : {e}")


    async def _scheduled_paper_report(self, hours: int = 24):
        logger.info(f" {hours}     ...")
        try:
            data = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: generate_paper_report(
                    hours=hours, output_dir="reports/paper",
                ),
            )
            m       = data.get("metrics", {})
            pnl     = m.get("total_pnl_pct", 0)
            sign    = "+" if pnl >= 0 else ""
            fg_line = ""
            if self.fear_greed.is_valid:
                fg_line = (
                    f"공포탐욕: {self.fear_greed.index} "
                    f"({self.fear_greed.label})\n"
                )
            btc_status = self.correlation_filter.get_btc_status()
            btc_line   = ""
            if btc_status.get("is_globally_blocked"):
                btc_line = (
                    f"⚠️ BTC 급락 차단 중 "
                    f"({btc_status['block_remaining_sec']}초 남음)\n"
                )
            msg = (
                f"📊 [{hours}시간 리포트]\n"
                f"수익률 : {sign}{pnl:.2f}%\n"
                f"승률   : {m.get('win_rate', 0):.1f}%\n"
                f"거래수 : {m.get('total_trades', 0)}회\n"
                f"샤프   : {m.get('sharpe_ratio', 0):.3f}\n"
                f"최대DD : -{m.get('max_drawdown_pct', 0):.2f}%\n"
                f"{fg_line}{btc_line}"
                f"리포트 : reports/paper/ 폴더 확인"
            )
            await self.telegram.send_message(msg)
            logger.success("✅ 페이퍼 리포트 생성 완료")
        except Exception as e:
            logger.error(f"   : {e}")


    async def _scheduled_kimchi_update(self):
        try:
            await self.kimchi_monitor.fetch_all()
            summary = self.kimchi_monitor.get_summary()
            try:
                premium_val = (
                    summary.get("premium_pct")
                    if isinstance(summary, dict)
                    else None
                )
                if premium_val is None and hasattr(
                    self.kimchi_monitor, "premium_pct"
                ):
                    premium_val = self.kimchi_monitor.premium_pct
                dashboard_state.signals["kimchi_premium"] = premium_val
            except Exception:
                pass
            logger.info(f"   : {summary}")
        except Exception as e:
            logger.warning(f"   : {e}")


    async def _scheduled_fear_greed_update(self):
        try:
            ok = await self.fear_greed.fetch()
            if ok:
                logger.info(
                    f"   : {self.fear_greed.index} "
                    f"({self.fear_greed.label})"
                )
                idx = self.fear_greed.index or 50
                if idx <= 15:
                    await self.telegram.send_message(
                        f"⚠️ 공포탐욕: 극도 공포 {idx} "
                        f"— 역발상 매수 기회 탐색 중"
                    )
                elif idx >= 85:
                    await self.telegram.send_message(
                        f"⚠️ 공포탐욕: 극도 탐욕 {idx} "
                        f"— 신규 매수 억제 모드"
                    )
        except Exception as e:
            logger.warning(f"   : {e}")


    async def _scheduled_walk_forward(self):
        logger.info("  Walk-Forward  ...")
        try:
            from backtesting.walk_forward import run_weekly_walk_forward
            results    = await run_weekly_walk_forward()
            profitable = [k for k, v in results.items() if v.is_profitable]
            msg = (
                f"🔬 Walk-Forward 완료\n"
                f"수익 전략: "
                f"{', '.join(profitable) if profitable else '없음'}\n"
                f"최적 파라미터 → config/optimized_params.json 저장"
            )
            await self.telegram.send_message(msg)
            # ✅ FIX: Walk-Forward 결과 DB 저장
            import datetime as _wf_dt
            _wf_summary = {
                "profitable_count": len(profitable),
                "profitable_strategies": ", ".join(profitable) if profitable else "없음",
                "total_strategies": len(results),
                "run_at": _wf_dt.datetime.now().isoformat(),
            }
            import json as _wf_json
            await self.db_manager.set_state(
                "walk_forward_last_result",
                _wf_json.dumps(_wf_summary, ensure_ascii=False)
            )
            logger.info(f"[Walk-Forward] DB 저장 완료: {_wf_summary}")
        except Exception as e:
            logger.error(f"Walk-Forward  : {e}")


    async def _scheduled_news_update(self):
        try:
            count = await self.news_analyzer.fetch_news()
            logger.debug(f"  : {count}")
        except Exception as e:
            logger.debug(f"  : {e}")


    async def _ws_reconnect_loop(self):
        RECONNECT_DELAY = 5
        MAX_DELAY       = 60
        delay = RECONNECT_DELAY
        while True:
            try:
                if self.ws_collector and not self.ws_collector.is_connected():
                    logger.warning(
                        f" WebSocket   → {delay}   "
                    )
                    
                    # ===== 시그널 평가 및 진입 로직 (v2.1.0) =====
                                # 데이터 가져오기
                                
                                # ML 점수 가져오기 (캐시 또는 새로 계산)
                                
                                # 시그널 평가
                            
                    
                    # =================================================

                    await asyncio.sleep(delay)
                    await self.ws_collector.reconnect()
                    logger.info(" WebSocket  ")
                    delay = RECONNECT_DELAY
                else:
                    delay = RECONNECT_DELAY
                    await asyncio.sleep(10)
            except Exception as e:
                logger.error(f" WebSocket  : {e}")
                delay = min(delay * 2, MAX_DELAY)
                await asyncio.sleep(delay)

    # ── 대시보드 상태 업데이트 ───────────────────────────────────

    async def _update_dashboard_state(self, krw: float, total_value: float):
        try:
            from monitoring.dashboard import dashboard_state  # [FIX] dashboard_state 임포트
            stats     = self.portfolio.get_statistics()
            daily_pnl = self.portfolio.get_daily_pnl(total_value)
            drawdown  = self.portfolio.get_current_drawdown(total_value)

            try:
                _ks = self.kimchi_monitor.get_summary()
                _kv = (
                    _ks.get("premium_pct")
                    if isinstance(_ks, dict)
                    else getattr(self.kimchi_monitor, "premium_pct", None)
                )
                if _kv is not None:
                    dashboard_state.signals["kimchi_premium"] = _kv
                _ns = self.news_analyzer.get_dashboard_summary()
                _gs = _ns.get("global_sentiment", None)
                if _gs is not None:
                    _nl = (
                        "Positive" if _gs >= 0.2 else
                        "Negative" if _gs <= -0.2 else
                        "Neutral"
                    )
                    dashboard_state.signals["news_sentiment"] = _nl
                    dashboard_state.signals["news_score"]     = round(float(_gs), 3)
                _fg = getattr(self.fear_greed, "index", None) or 50
                if _fg <= 25:
                    _regime = "BEAR"
                elif _fg >= 75:
                    _regime = "BULL"
                elif _fg <= 45:
                    _regime = "BEAR_WATCH"
                else:
                    _regime = "NEUTRAL"
                dashboard_state.signals["market_regime"] = _regime
            except Exception:
                pass

            _pos_dict = {}
            for _m, _pos in self.portfolio.open_positions.items():
                _cp  = getattr(_pos, "current_price", None) or _pos.entry_price
                _pnl = (_cp - _pos.entry_price) / _pos.entry_price * 100
                _pos_dict[_m] = {
                    "entry_price":        _pos.entry_price,
                    "current_price":      _cp,
                    "volume":             _pos.volume,
                    "unrealized_pnl_pct": round(_pnl, 2),
                    "hold_hours":         0.0,
                    "strategy":           getattr(_pos, "strategy", "-"),
                }
            dashboard_state.portfolio.update({
                "total_krw":   round(total_value, 2),
                "krw_balance": round(krw, 2),
                "positions":   _pos_dict,
                "pnl_today":   round(daily_pnl, 4),
                "type":        "portfolio",
            })

            positions_detail = []
            for market, pos in self.portfolio.open_positions.items():
                cur_price = getattr(pos, "current_price", None) or pos.entry_price
                invested  = round(pos.entry_price * pos.volume, 0)
                positions_detail.append({
                    "market":        market,
                    "strategy":      getattr(pos, "strategy", "-"),
                    "entry_price":   pos.entry_price,
                    "current_price": cur_price,
                    "amount_krw":    invested,
                    "profit_rate":   round(pos.unrealized_pnl_pct / 100, 4),
                    "take_profit":   getattr(pos, "take_profit", None),
                    "stop_loss":     getattr(pos, "stop_loss",   None),
                })
            invested_total = sum(p["amount_krw"] for p in positions_detail)

            dashboard_state.portfolio.update({
                "total_assets":     round(total_value, 0),
                "cash":             round(krw, 0),
                "invested":         round(invested_total, 0),
                "positions":        len(positions_detail),
                "positions_detail": positions_detail,
                "mode":             (
                    "PAPER"
                    if getattr(self, "mode", "paper") == "paper"
                    else "LIVE"
                ),
                "pnl": round(daily_pnl, 0),
            })

            dashboard_state.metrics.update({
                "daily_pnl":      daily_pnl,
                "total_trades":   stats.get("total_trades",  0),
                "win_rate":       stats.get("win_rate",       0),
                "profit_factor":  stats.get("profit_factor",  0),
                "max_drawdown":   drawdown,
                "sharpe_ratio":   stats.get("sharpe_ratio",   0),
                "strategy_stats": stats.get("strategy_stats", []),
            })

            kimchi_pct = None
            try:
                premiums = self.kimchi_monitor.get_all_premiums()
                if premiums:
                    vals       = [v for v in premiums.values() if v is not None]
                    kimchi_pct = round(sum(vals) / len(vals), 2) if vals else None
            except Exception:
                pass

            bear_count = getattr(self, "_bear_reversal_today", 0)
            btc_status = self.correlation_filter.get_btc_status()

            news_label = "--"
            try:
                ns    = self.news_analyzer.get_dashboard_summary()
                score = ns.get("global_sentiment", 0.0)
                news_label = (
                    "긍정적" if score >  0.3 else
                    "부정적" if score < -0.3 else
                    "중립"
                )
            except Exception:
                pass

            last_regime = "--"
            try:
                last_regime = getattr(self, "_last_regime", "--")
            except Exception:
                pass

            dashboard_state.signals.update({
                "fear_greed":          self.fear_greed.index,
                "fear_greed_label":    self.fear_greed.label,
                "kimchi_premium":      kimchi_pct,
                "news_sentiment":      news_label,
                "market_regime":       last_regime,
                "bear_reversal_count": bear_count,
                "btc_shock_blocked":   btc_status.get("is_globally_blocked", False),
            })

        except Exception as _e:
            logger.debug(f"  : {_e}")


    def _get_hold_hours(self, market: str) -> float:
        """포지션 보유 시간(시간)을 반환."""
        try:
            pos = self._portfolio.get(market) or {}
            entry_time = pos.get("entry_time") or pos.get("timestamp")
            if entry_time is None:
                return 0.0
            if isinstance(entry_time, str):
                entry_time = _dt.fromisoformat(entry_time)
            elif isinstance(entry_time, (int, float)):
                ts = entry_time / 1000 if entry_time > 1e10 else entry_time
                entry_time = _dt.fromtimestamp(ts)
            return (_dt2.now() - entry_time).total_seconds() / 3600
        except Exception:
            return 0.0


    def _time_based_tp_threshold(self, market: str) -> float:
        """보유 시간별 익절 기준 반환.
        0-6h  : +1.5%
        6-24h : +0.8%
        >24h  : +0.3%
        """
        h = self._get_hold_hours(market)
        if h >= 48:
            return -999.0  # 48h+ 강제청산 (손실도 감수)
        elif h >= 24:
            return 0.5     # 24~48h: +0.5% 익절
        elif h >= 6:
            return 0.8     # 6~24h: +0.8% 익절
        return 1.5         # 0~6h: +1.5% 익절