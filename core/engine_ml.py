"""
core/engine_ml.py
─────────────────────────────────────────────────────────────
ML / PPO 예측 및 모델 관련 Mixin

포함 메서드:
    _get_ml_prediction        : 단일 코인 ML 예측
    _get_ml_prediction_batch  : 다중 코인 배치 ML 예측
    _get_ppo_prediction       : PPO 강화학습 예측
    _load_ml_model            : ML 모델 로드
    _init_ppo_agent           : PPO 에이전트 초기화
    _auto_train_ppo           : PPO 자동 훈련
    _run_auto_retrain         : ML 앙상블 자동 재훈련
─────────────────────────────────────────────────────────────
"""
from __future__ import annotations
from typing import Optional
import asyncio
from loguru import logger


class EngineMLMixin:
    """ML 예측, 모델 로드, PPO 에이전트 관련 메서드 Mixin"""

    async def _get_ml_prediction(self, market: str, df) -> Optional[dict]:
        if self._ml_predictor is None:
            return None
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._ml_predictor.predict, market, df
            )
            if result:
                from monitoring.dashboard import dashboard_state
                from datetime import datetime
                _sig  = result.get("signal",     "HOLD")
                _conf = result.get("confidence", 0.0)
                _bp   = result.get("buy_prob",   0.0)
                _sp   = result.get("sell_prob",  0.0)
                _ml_pred_data = {
                    "signal":     _sig,
                    "confidence": round(float(_conf), 3),
                    "buy_prob":   round(float(_bp),   3),
                    "sell_prob":  round(float(_sp),   3),
                    "market":     market,
                }
                if "ml_predictions" not in dashboard_state.signals:
                    dashboard_state.signals["ml_predictions"] = {}
                dashboard_state.signals["ml_predictions"][market] = {
                    "signal":          result.get("signal"),
                    "confidence":      round(result.get("confidence", 0), 4),
                    "buy_prob":        round(result.get("buy_prob",   0), 4),
                    "hold_prob":       round(result.get("hold_prob",  0), 4),
                    "sell_prob":       round(result.get("sell_prob",  0), 4),
                    "model_agreement": round(result.get("model_agreement", 0), 4),
                    "inference_ms":    round(result.get("inference_ms",    0), 2),
                    "updated_at":      datetime.now().strftime("%H:%M:%S"),
                }
                dashboard_state.signals["ml_predictions"][market] = _ml_pred_data
                dashboard_state.signals["ml_prediction"]           = _ml_pred_data
                dashboard_state.signals["ml_last_updated"] = (
                    datetime.now().isoformat()
                )
                dashboard_state.signals["ml_model_loaded"] = (
                    self._ml_predictor._is_loaded
                )
            return result
        except Exception as e:
            logger.error(f"ML   ({market}): {e}")
            return None


    async def _get_ml_prediction_batch(self, market_df_map: dict) -> dict:
        if self._ml_predictor is None:
            return {}
        try:
            t_start = __import__("time").perf_counter()
            results = await asyncio.get_event_loop().run_in_executor(
                None, self._ml_predictor.predict_batch, market_df_map,
            )
            elapsed = (__import__("time").perf_counter() - t_start) * 1000
            if results:
                logger.info(
                    f"  ML  : {len(results)}개 코인 | "
                    f"{elapsed:.1f}ms | "
                    f"코인당 {elapsed/len(results):.1f}ms"
                )
                try:
                    from monitoring.dashboard import dashboard_state
                    from datetime import datetime
                    if "ml_predictions" not in dashboard_state.signals:
                        dashboard_state.signals["ml_predictions"] = {}
                    for mkt, res in results.items():
                        dashboard_state.signals["ml_predictions"][mkt] = {
                            "signal":          res.get("signal"),
                            "confidence":      round(res.get("confidence", 0), 4),
                            "buy_prob":        round(res.get("buy_prob",   0), 4),
                            "hold_prob":       round(res.get("hold_prob",  0), 4),
                            "sell_prob":       round(res.get("sell_prob",  0), 4),
                            "model_agreement": round(res.get("model_agreement", 0), 4),
                            "updated_at":      datetime.now().strftime("%H:%M:%S"),
                        }
                    dashboard_state.signals["ml_last_updated"] = (
                        datetime.now().isoformat()
                    )
                    dashboard_state.signals["ml_model_loaded"] = (
                        self._ml_predictor._is_loaded
                    )
                except Exception as _db_e:
                    logger.debug(f" ML   : {_db_e}")
            return results
        except Exception as e:
            logger.warning(f" ML   →   : {e}")
            return {}


    async def _get_ppo_prediction(self, market: str, df) -> Optional[dict]:
        if self._ppo_agent is None or not self._ppo_agent._is_trained:
            return None
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._ppo_agent.predict_from_df(df, market)
            )
        except Exception as e:
            logger.debug(f"PPO   ({market}): {e}")
            return None

    # ── 매수 실행 ────────────────────────────────────────────────
    

    async def _load_ml_model(self):
        try:
            from models.inference.predictor import MLPredictor
            self._ml_predictor = MLPredictor()
            ok = await asyncio.get_event_loop().run_in_executor(
                None, self._ml_predictor.load_model
            )
            if ok and self._device == "cuda" and self._ml_predictor._model is not None:
                self._ml_predictor._model = maybe_compile(
                    self._ml_predictor._model,
                    backend="eager",
                    mode="default",
                )
            log_gpu_status()
            logger.success("✅ ML 앙상블 모델 로드 완료")
        except Exception as e:
            logger.warning(f"ML    (   ): {e}")

    # ── 마켓 스캐너 ──────────────────────────────────────────────

    async def _init_ppo_agent(self):
        try:
            from models.rl.ppo_agent import PPOTradingAgent, check_ppo_dependencies
            deps = check_ppo_dependencies()
            if not all(deps.values()):
                missing = [k for k, v in deps.items() if not v]
                logger.info(
                    f" PPO    (: {missing})"
                )
                return
            self._ppo_agent = PPOTradingAgent(use_gpu=(self._device == "cuda"))
            loaded = self._ppo_agent.load_model()
            if loaded:
                logger.success("✅ PPO 모델 로드 완료 (저장된 비중 사용)")
            else:
                logger.info(" PPO   —       ")
                from datetime import datetime, timedelta
                self.scheduler.add_job(
                    self._auto_train_ppo, "date",
                    run_date=datetime.now() + timedelta(minutes=10),
                    id="ppo_initial_train",
                )
                logger.info(" PPO  :   10   ")
        except Exception as e:
            logger.warning(f"PPO   ( ): {e}")


    async def _auto_train_ppo(
        self, total_timesteps: int = 200_000, notify: bool = True
    ):
        logger.info(" PPO    —   ...")
        if notify:
            await self.telegram.send_message(
                f"🤖 PPO 강화학습 훈련 시작\n"
                f"  대상 코인: "
                f"{', '.join(self.settings.trading.target_markets)}\n"
                f"  에피소드: {total_timesteps:,}스텝\n"
                f"  완료 시 텔레그램 알림 (약 15분 소요)"
            )
        try:
            from models.rl.ppo_agent import PPOTradingAgent
            from data.processors.candle_processor import CandleProcessor
            import pandas as pd

            markets   = self.settings.trading.target_markets
            processor = CandleProcessor()

            logger.info("     ...")
            raw_dfs = []
            for m in markets:
                try:
                    df = await self.rest_collector.get_ohlcv(m, "minute60", 500)
                    raw_dfs.append(df)
                except Exception as e:
                    raw_dfs.append(e)
                await asyncio.sleep(0.35)

            processed_dfs = []
            for i, df in enumerate(raw_dfs):
                if isinstance(df, Exception) or df is None:
                    continue
                try:
                    p = await processor.process(markets[i], df, "60")
                    if p is not None and len(p) > 100:
                        processed_dfs.append(p)
                except Exception:
                    pass

            if not processed_dfs:
                logger.warning("PPO    —  ")
                return

            combined_df = pd.concat(processed_dfs, ignore_index=True)
            logger.info(
                f"   : {len(combined_df)}샘플 "
                f"({len(processed_dfs)}개 코인)"
            )

            agent  = PPOTradingAgent(use_gpu=(self._device == "cuda"))
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: agent.train(
                    combined_df, total_timesteps=total_timesteps
                ),
            )

            if "error" not in result:
                self._ppo_agent = agent
                logger.success(
                    f"✅ PPO 자동 훈련 완료 | "
                    f"PnL={result.get('pnl_pct',0):+.2f}% | "
                    f"승률={result.get('win_rate',0):.1f}% | "
                    f"샤프={result.get('sharpe',0):.3f}"
                )
                if notify:
                    await self.telegram.send_message(
                        f"✅ PPO 훈련 완료\n"
                        f"  PnL  : {result.get('pnl_pct',0):+.2f}%\n"
                        f"  승률 : {result.get('win_rate',0):.1f}%\n"
                        f"  샤프 : {result.get('sharpe',0):.3f}\n"
                        f"  모델 : models/saved/ppo/ 저장됨\n"
                        f"  다음 재훈련: 매주 월요일 03:00"
                    )
                self.scheduler.add_job(
                    lambda: asyncio.create_task(
                        self._auto_train_ppo(total_timesteps)
                    ),
                    "cron",
                    day_of_week="mon", hour=3, minute=0,
                    id="ppo_weekly_retrain",
                    replace_existing=True,
                )
            else:
                logger.warning(f"PPO  : {result.get('error')}")

        except Exception as e:
            logger.error(f"PPO   : {e}")


    async def _run_auto_retrain(self):
        try:
            logger.info("[AutoTrainer]     ...")
            result = await self.auto_trainer.run_if_needed()
            if result:
                await self._load_ml_model()
                logger.info("[AutoTrainer]    +   ")
            else:
                logger.info("[AutoTrainer]      ")
        except Exception as e:
            logger.error(f"[AutoTrainer] : {e}")

