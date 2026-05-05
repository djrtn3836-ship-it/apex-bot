"""
APEX BOT - PPO 강화학습 에이전트
ML 앙상블 다음 단계: 시장 환경과 직접 상호작용하며 자가 학습

아키텍처:
  - 환경: TradingEnv (gym.Env)  ← Upbit OHLCV 데이터
  - 에이전트: PPO (Proximal Policy Optimization)
  - 상태 공간: 120개 기술 지표 + 포지션 정보
  - 행동 공간: 0=HOLD, 1=BUY, 2=SELL (3개 이산 행동)
  - 보상: 실현 수익률 - 수수료 - DD 페널티

사용법:
  agent = PPOTradingAgent()
  agent.train(df, episodes=1000)
  action, confidence = agent.predict(state)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

# ── Gymnasium/Gym ─────────────────────────────────────────────
try:
    import gymnasium as gym
    from gymnasium import spaces
    GYM_AVAILABLE = True
except ImportError:
    try:
        import gym
        from gym import spaces
        GYM_AVAILABLE = True
    except ImportError:
        GYM_AVAILABLE = False
        logger.warning("gymnasium/gym 미설치 → PPO 에이전트 비활성화")

# ── Stable Baselines 3 ────────────────────────────────────────
try:
    from stable_baselines3 import PPO
    from stable_baselines3.common.vec_env import DummyVecEnv
    from stable_baselines3.common.callbacks import (
        EvalCallback, StopTrainingOnRewardThreshold
    )
    SB3_AVAILABLE = True
except ImportError:
    SB3_AVAILABLE = False
    logger.warning("stable-baselines3 미설치 → PPO 에이전트 비활성화")

# ── PyTorch ───────────────────────────────────────────────────
try:
    import torch
    import torch.nn as nn
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


MODEL_DIR = Path("models/saved/ppo")
MODEL_DIR.mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────────────────────
#  거래 환경 (Gymnasium)
# ──────────────────────────────────────────────────────────────

if GYM_AVAILABLE:
    class TradingEnv(gym.Env):
        """
        업비트 페이퍼 트레이딩 환경

        상태(Observation):
          - 60개 캔들 × 8개 OHLCV+지표 = 480차원  (또는 120 특징)
          - 포지션 정보: [보유여부, 수익률, 보유시간] = 3차원
          - 합계: 123차원

        행동(Action):
          - 0: HOLD
          - 1: BUY  (전체 잔금의 20% 매수)
          - 2: SELL (전량 매도)

        보상(Reward):
          - BUY  → 0 (즉각 보상 없음)
          - HOLD → unrealized P&L 변화율 * 0.1
          - SELL → realized P&L - 수수료 - DD 페널티
        """
        metadata = {"render_modes": ["human"]}

        FEE_RATE   = 0.001   # 0.1% 편도
        INITIAL_CAPITAL = 1_000_000  # 100만원

        def __init__(self, df: pd.DataFrame, feature_cols: Optional[List[str]] = None):
            super().__init__()
            self.df = df.reset_index(drop=True)
            self.feature_cols = feature_cols or self._default_features()
            self.n_features = len(self.feature_cols) + 6  # +3 position +3 market context

            # Gymnasium 공간 정의
            self.observation_space = spaces.Box(
                low=-np.inf, high=np.inf,
                shape=(self.n_features,), dtype=np.float32
            )
            self.action_space = spaces.Discrete(3)  # 0:HOLD, 1:BUY, 2:SELL

            self._reset_state()

        def _default_features(self) -> List[str]:
            """기본 특징 컬럼 (데이터에 있는 것만 사용)"""
            candidates = [
                "open", "high", "low", "close", "volume",
                "rsi", "macd", "macd_signal", "macd_hist",
                "bb_upper", "bb_mid", "bb_lower", "bb_pct",
                "atr", "adx", "cci", "mfi",
                # [PP-1 FIX] candle_processor는 ema{p} (언더스코어 없음) 저장
                "ema5", "ema20", "ema50", "sma20",
                "stoch_k", "stoch_d", "williams_r",
                "obv", "vwap", "momentum_10",
            ]
            return [c for c in candidates if c in self.df.columns]

        def _reset_state(self):
            self.current_step = 60  # 최소 60개 이전 데이터 필요
            self.capital = self.INITIAL_CAPITAL
            self.position = 0.0   # 보유 코인 수량
            self.entry_price = 0.0
            self.peak_value = self.INITIAL_CAPITAL
            self.done = False
            self.trade_count = 0
            self.win_count = 0

        def reset(self, seed=None, options=None):
            super().reset(seed=seed)
            self._reset_state()
            obs = self._get_obs()
            return obs, {}

        def step(self, action: int):
            assert not self.done
            current_price = float(self.df["close"].iloc[self.current_step])
            reward = 0.0
            info = {}

            if action == 1:  # BUY
                if self.position == 0:
                    self._entry_step = self.current_step  # 진입 시점 기록
                    invest = self.capital * 0.20  # 20% 투자
                    fee = invest * self.FEE_RATE
                    self.position = (invest - fee) / current_price
                    self.entry_price = current_price
                    self.capital -= invest
                    info["trade"] = "BUY"

            elif action == 2:  # SELL
                if self.position > 0:
                    proceeds = self.position * current_price
                    fee = proceeds * self.FEE_RATE
                    net = proceeds - fee
                    pnl = net - (self.position * self.entry_price)
                    pnl_pct = pnl / (self.position * self.entry_price)

                    # 수익률 보상 + 보유시간 패널티 + 승리 보너스
                    hold_steps = self.current_step - getattr(self, '_entry_step', self.current_step)
                    hold_penalty = hold_steps * 0.002  # 봉당 -0.002% 패널티
                    win_bonus = 0.5 if pnl_pct > 0 else 0.0  # 승리 보너스
                    reward = pnl_pct * 100 - hold_penalty + win_bonus
                    if pnl > 0:
                        self.win_count += 1
                    self.trade_count += 1
                    self.capital += net
                    self.position = 0.0
                    self.entry_price = 0.0
                    info["trade"] = "SELL"
                    info["pnl_pct"] = pnl_pct

            else:  # HOLD
                if self.position > 0:
                    # 미실현 수익 변화 → 아주 작은 보상 (홀딩 억제)
                    unr_pnl = (current_price - self.entry_price) / self.entry_price
                    hold_steps = self.current_step - getattr(self, '_entry_step', self.current_step)
                    hold_penalty = hold_steps * 0.001  # 봉당 -0.001% 보유 패널티
                    reward = unr_pnl * 0.01 - hold_penalty  # 홀딩 보상 0.05→0.01로 축소

            # DD 페널티
            total_value = self.capital + self.position * current_price
            self.peak_value = max(self.peak_value, total_value)
            drawdown = (self.peak_value - total_value) / self.peak_value
            if drawdown > 0.10:
                reward -= drawdown * 50  # DD 10% 초과 시 강한 페널티

            self.current_step += 1
            terminated = self.current_step >= len(self.df) - 1
            truncated = False
            self.done = terminated

            obs = self._get_obs()
            return obs, float(reward), terminated, truncated, info

        def _get_obs(self) -> np.ndarray:
            """현재 상태 벡터"""
            row = self.df.iloc[self.current_step]
            features = []
            for col in self.feature_cols:
                val = float(row.get(col, 0.0))
                if np.isnan(val) or np.isinf(val):
                    val = 0.0
                features.append(val)

            # 정규화 (close 기준)
            close = float(row["close"]) if "close" in row.index else 1.0
            features_norm = [f / close if close > 0 else f for f in features]

            # 포지션 정보 추가
            in_position = 1.0 if self.position > 0 else 0.0
            unrealized_pnl = 0.0
            if self.position > 0 and self.entry_price > 0:
                unrealized_pnl = (close - self.entry_price) / self.entry_price
            hold_ratio = (self.current_step - 60) / len(self.df)

            # 시장 컨텍스트 추가 (FearGreed, regime, kimchi)
            fg = float(getattr(self, 'fear_greed', 50)) / 100.0  # 0~1 정규화
            regime_map = {'TRENDING_UP': 1.0, 'TRENDING_DOWN': -1.0,
                          'RANGING': 0.0, 'VOLATILE': 0.5}
            regime = regime_map.get(getattr(self, 'regime', 'RANGING'), 0.0)
            kimchi = float(getattr(self, 'kimchi_premium', 0.0)) / 10.0  # 정규화

            obs = np.array(
                features_norm + [in_position, unrealized_pnl, hold_ratio,
                                 fg, regime, kimchi],
                dtype=np.float32
            )
            return obs

        def render(self):
            current_price = float(self.df["close"].iloc[self.current_step])
            total = self.capital + self.position * current_price
            pnl_pct = (total / self.INITIAL_CAPITAL - 1) * 100
            print(
                f"Step={self.current_step} | "
                f"총자산={total:,.0f} ({pnl_pct:+.2f}%) | "
                f"포지션={self.position:.4f} | "
                f"승률={self.win_count}/{self.trade_count}"
            )


# ──────────────────────────────────────────────────────────────
#  PPO 트레이딩 에이전트
# ──────────────────────────────────────────────────────────────

class PPOTradingAgent:
    """
    PPO 강화학습 트레이딩 에이전트

    ML 앙상블과의 차이점:
      ML  : 과거 패턴 학습 → 정적 예측
      PPO : 환경과 상호작용 → 동적 정책 최적화 (탐색+활용)

    통합 방법:
      1. PPO 신호 (confidence 포함) → SignalCombiner 추가 입력
      2. ML 앙상블과 앙상블 (소프트 보팅)
      3. 완전 독립 실행 (실험 모드)
    """

    MODEL_NAME = "ppo_trading"

    def __init__(
        self,
        use_gpu: bool = True,
        tensorboard_log: str = None,  # tensorboard 미사용
    ):
        self.use_gpu = use_gpu and TORCH_AVAILABLE
        self.tensorboard_log = tensorboard_log
        self._model: Optional["PPO"] = None
        self._env: Optional["TradingEnv"] = None
        self._is_trained = False

        if not GYM_AVAILABLE:
            logger.warning("PPO: gymnasium 미설치 → predict() 호출 시 None 반환")
        if not SB3_AVAILABLE:
            logger.warning("PPO: stable-baselines3 미설치 → 훈련 불가")

    # ── 학습 ────────────────────────────────────────────────────

    def train(
        self,
        df: pd.DataFrame,
        total_timesteps: int = 200_000,
        eval_freq: int = 10_000,
    ) -> Dict:
        """
        PPO 에이전트 훈련

        Args:
            df: 전처리된 OHLCV + 지표 데이터프레임
            total_timesteps: 총 학습 스텝 수
            eval_freq: 평가 주기

        Returns:
            훈련 요약 딕셔너리
        """
        if not (GYM_AVAILABLE and SB3_AVAILABLE):
            logger.error("PPO 훈련 실패: 라이브러리 미설치")
            return {"error": "라이브러리 미설치"}

        logger.info(
            f"🤖 PPO 훈련 시작 | "
            f"데이터={len(df)}개 캔들 | "
            f"스텝={total_timesteps:,}"
        )

        try:
            # 훈련/검증 분할 (80/20)
            split = int(len(df) * 0.8)
            df_train = df.iloc[:split].copy()
            df_eval  = df.iloc[split:].copy()

            # 환경 생성
            train_env = DummyVecEnv([lambda: TradingEnv(df_train)])
            eval_env  = DummyVecEnv([lambda: TradingEnv(df_eval)])

            # PPO 하이퍼파라미터
            device = "cpu"  # MlpPolicy는 GPU보다 CPU가 빠름 (SB3 권장)
            policy_kwargs = dict(
                net_arch=[dict(pi=[256, 128, 64], vf=[256, 128, 64])],
                activation_fn=nn.Tanh if TORCH_AVAILABLE else None,
            )

            self._model = PPO(
                policy="MlpPolicy",
                env=train_env,
                learning_rate=3e-4,
                n_steps=2048,
                batch_size=64,
                n_epochs=10,
                gamma=0.99,
                gae_lambda=0.95,
                clip_range=0.2,
                ent_coef=0.01,       # 탐색 장려
                vf_coef=0.5,
                max_grad_norm=0.5,
                # tensorboard_log 비활성화 (tensorboard 미설치)
                device=device,
                verbose=0,
                policy_kwargs=policy_kwargs if TORCH_AVAILABLE else {},
            )

            # 콜백
            reward_threshold = StopTrainingOnRewardThreshold(
                reward_threshold=50,  # 평균 누적 보상 50 이상이면 조기 종료
                verbose=1,
            )
            eval_callback = EvalCallback(
                eval_env,
                best_model_save_path=str(MODEL_DIR),
                log_path=str(MODEL_DIR),
                eval_freq=eval_freq,
                callback_on_new_best=reward_threshold,
                verbose=0,
            )

            self._model.learn(
                total_timesteps=total_timesteps,
                callback=eval_callback,
            )

            # 모델 저장
            save_path = MODEL_DIR / self.MODEL_NAME
            self._model.save(str(save_path))
            self._is_trained = True

            # 최종 평가
            eval_result = self._evaluate(df_eval)
            logger.success(
                f"✅ PPO 훈련 완료 | "
                f"샤프={eval_result.get('sharpe', 0):.3f} | "
                f"승률={eval_result.get('win_rate', 0):.1f}% | "
                f"PnL={eval_result.get('pnl_pct', 0):+.2f}%"
            )
            return eval_result

        except Exception as e:
            logger.error(f"PPO 훈련 오류: {e}")
            return {"error": str(e)}

    # ── 추론 ────────────────────────────────────────────────────

    def predict(
        self, state: np.ndarray
    ) -> Tuple[Optional[str], float]:
        """
        현재 상태에서 행동 예측

        Args:
            state: 관측 벡터 (n_features,)

        Returns:
            (action_str, confidence)
            action_str: "BUY" / "HOLD" / "SELL"
            confidence: 0.0 ~ 1.0
        """
        if not self._is_trained or self._model is None:
            return None, 0.0

        try:
            action, _states = self._model.predict(
                state.reshape(1, -1), deterministic=True
            )
            action = int(action)

            # 행동 확률 추출 (PPO policy)
            obs_tensor = self._model.policy.obs_to_tensor(
                state.reshape(1, -1)
            )[0]
            with (torch.no_grad() if TORCH_AVAILABLE else _no_grad_ctx()):
                dist = self._model.policy.get_distribution(obs_tensor)
                probs = dist.distribution.probs.cpu().numpy()[0]
            confidence = float(probs[action])

            action_map = {0: "HOLD", 1: "BUY", 2: "SELL"}
            return action_map[action], confidence

        except Exception as e:
            logger.debug(f"PPO predict 오류: {e}")
            return None, 0.0

    def predict_from_df(
        self, df: pd.DataFrame, market: str
    ) -> Optional[Dict]:
        """
        DataFrame에서 직접 ML Predictor 형식으로 반환
        (SignalCombiner 입력 형식과 호환)
        """
        if not self._is_trained:
            return None

        try:
            env = TradingEnv(df)
            state = env._get_obs()
            action_str, confidence = self.predict(state)

            if action_str is None:
                return None

            from strategies.base_strategy import SignalType
            signal_map = {
                "BUY":  SignalType.BUY,
                "SELL": SignalType.SELL,
                "HOLD": SignalType.HOLD,
            }

            return {
                "signal":     signal_map.get(action_str, SignalType.HOLD),
                "confidence": confidence,
                "source":     "PPO_RL",
                "action":     action_str,
            }
        except Exception as e:
            logger.debug(f"PPO predict_from_df 오류: {e}")
            return None

    # ── 모델 로드/저장 ───────────────────────────────────────────

    def load_model(self) -> bool:
        """저장된 모델 로드"""
        if not SB3_AVAILABLE:
            return False

        # best_model.zip 우선, 없으면 ppo_trading.zip
        candidates = [
            MODEL_DIR / "best_model.zip",
            MODEL_DIR / f"{self.MODEL_NAME}.zip",
        ]
        for path in candidates:
            if path.exists():
                try:
                    self._model = PPO.load(str(path), device="cpu")
                    self._is_trained = True
                    logger.info(f"✅ PPO 모델 로드: {path}")
                    return True
                except Exception as e:
                    logger.warning(f"PPO 모델 로드 실패 ({path}): {e}")

        logger.info("PPO 모델 없음 — 훈련 필요")
        return False

    # ── 평가 ────────────────────────────────────────────────────

    def _evaluate(self, df: pd.DataFrame, n_episodes: int = 3) -> Dict:
        """검증 데이터로 성과 평가"""
        if not (GYM_AVAILABLE and SB3_AVAILABLE and self._model):
            return {}

        all_pnls = []
        all_wins = []
        all_trades = []

        for _ in range(n_episodes):
            env = TradingEnv(df)
            obs, _ = env.reset()
            done = False
            while not done:
                action, _ = self._model.predict(obs, deterministic=True)
                obs, _, terminated, truncated, _ = env.step(int(action))
                done = terminated or truncated

            final_val = env.capital + env.position * float(df["close"].iloc[-1])
            pnl = (final_val / TradingEnv.INITIAL_CAPITAL - 1) * 100
            all_pnls.append(pnl)
            all_wins.append(env.win_count)
            all_trades.append(env.trade_count)

        avg_pnl = sum(all_pnls) / len(all_pnls)
        total_trades = sum(all_trades)
        total_wins = sum(all_wins)
        win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0

        # 간단 샤프
        pnl_series = pd.Series(all_pnls)
        sharpe = (pnl_series.mean() / pnl_series.std()) if pnl_series.std() > 0 else 0

        return {
            "pnl_pct": round(avg_pnl, 2),
            "win_rate": round(win_rate, 1),
            "trades": total_trades // n_episodes,
            "sharpe": round(sharpe, 3),
        }


# ──────────────────────────────────────────────────────────────
#  설치 안내
# ──────────────────────────────────────────────────────────────

def check_ppo_dependencies() -> Dict[str, bool]:
    """PPO 의존성 확인"""
    return {
        "gymnasium": GYM_AVAILABLE,
        "stable_baselines3": SB3_AVAILABLE,
        "torch": TORCH_AVAILABLE,
    }


def install_guide() -> str:
    return """
PPO 에이전트 활성화 방법:
  pip install gymnasium stable-baselines3[extra] torch

GPU 가속 (RTX 5060):
  pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
"""


# ──────────────────────────────────────────────────────────────
#  context manager placeholder (torch 미설치 시)
# ──────────────────────────────────────────────────────────────

class _no_grad_ctx:
    def __enter__(self): return self
    def __exit__(self, *_): pass


# ──────────────────────────────────────────────────────────────
#  CLI 훈련 실행
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    import os
    os.environ.setdefault("UPBIT_ACCESS_KEY", "test")
    os.environ.setdefault("UPBIT_SECRET_KEY", "test")

    deps = check_ppo_dependencies()
    print("=== PPO 의존성 확인 ===")
    for lib, ok in deps.items():
        print(f"  {lib}: {'✅' if ok else '❌ 미설치'}")

    if not all(deps.values()):
        print(install_guide())
    else:
        async def _train():
            from data.collectors.rest_collector import RestCollector
            from data.processors.candle_processor import CandleProcessor

            collector = RestCollector()
            processor = CandleProcessor()

            df = await collector.get_ohlcv("KRW-BTC", "minute60", 500)
            if df is None:
                print("데이터 수집 실패")
                return

            df_proc = await processor.process("KRW-BTC", df, "60")
            if df_proc is None:
                df_proc = df

            agent = PPOTradingAgent(use_gpu=True)
            result = agent.train(df_proc, total_timesteps=50_000)
            print(f"\n훈련 결과: {result}")

        asyncio.run(_train())
