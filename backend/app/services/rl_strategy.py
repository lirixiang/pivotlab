"""Reinforcement Learning trading strategy using PPO.

Unlike supervised models that learn from labeled buy/sell points,
the RL agent learns by interacting with a simulated market environment,
maximizing cumulative profit through trial and error.

Architecture:
  - TradingEnv (gymnasium.Env): simulates trading with real candle data
  - PPO (stable-baselines3): policy gradient agent with clipped objective
  - State: 31 technical features + position info (3 dims) = 34 dims
  - Actions: 0=hold, 1=buy, 2=sell
  - Reward: realized PnL on exit, with shaping for risk management
"""
from __future__ import annotations

import logging
import pickle
import time as _time
from pathlib import Path

import gymnasium as gym
import numpy as np
from gymnasium import spaces
from stable_baselines3.common.callbacks import BaseCallback

from ..schemas import Candle
from .ai_strategy import FEATURE_NAMES, NUM_FEATURES, extract_features, _MODEL_DIR

logger = logging.getLogger(__name__)


class _ProgressCallback(BaseCallback):
    """Log training progress every N% of total timesteps."""

    def __init__(self, total: int, pct: int = 10, progress_cb=None):
        super().__init__()
        self.total = total
        self.step_interval = max(total * pct // 100, 1)
        self.next_log = self.step_interval
        self.t0 = _time.time()
        self.progress_cb = progress_cb

    def _on_step(self) -> bool:
        if self.num_timesteps >= self.next_log:
            elapsed = _time.time() - self.t0
            pct_done = self.num_timesteps * 100 / self.total
            rate = self.num_timesteps / elapsed if elapsed > 0 else 0
            eta = (self.total - self.num_timesteps) / rate if rate > 0 else 0
            msg = f"RL训练: {self.num_timesteps}/{self.total} ({pct_done:.0f}%) | {rate:.0f} steps/s | ETA {eta:.0f}s"
            logger.info(
                "RL training: %d/%d (%.0f%%) | %.0f steps/s | ETA %.0fs",
                self.num_timesteps, self.total, pct_done, rate, eta,
            )
            if self.progress_cb:
                self.progress_cb(pct_done, msg)
            self.next_log += self.step_interval
        return True


# ─────────────────────────── Trading Environment ───────────────────────────

class TradingEnv(gym.Env):
    """Gym environment that simulates stock trading.

    State (34 dims):
      - 31 technical features (same as other models)
      - in_position: 0 or 1
      - unrealized_pnl_pct: current floating PnL as percentage
      - holding_bars_norm: days held / max_hold (normalized)

    Actions:
      0 = hold (do nothing)
      1 = buy (enter long position)
      2 = sell (exit position)

    Rewards:
      - Buy when flat: small cost (commission)
      - Sell with profit: +PnL%
      - Sell with loss: PnL% (negative)
      - Hold in position: tiny reward/penalty based on unrealized PnL direction
      - Forced exit at max_hold: PnL with penalty
      - Invalid action (buy when holding, sell when flat): -0.1 penalty
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        candles: list[Candle],
        commission_pct: float = 0.15,
        max_hold_bars: int = 20,
        start_idx: int = 60,
    ):
        super().__init__()
        self.candles = candles
        self.commission_pct = commission_pct / 100
        self.max_hold_bars = max_hold_bars
        self.start_idx = start_idx

        # State: 31 features + 3 position features
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf,
            shape=(NUM_FEATURES + 3,), dtype=np.float32,
        )
        # Actions: hold=0, buy=1, sell=2
        self.action_space = spaces.Discrete(3)

        # Internal state
        self.current_idx = start_idx
        self.in_position = False
        self.entry_price = 0.0
        self.entry_idx = 0
        self.total_pnl = 0.0
        self.trades: list[dict] = []

    def _get_obs(self) -> np.ndarray:
        feat = extract_features(self.candles, self.current_idx)
        if feat is None:
            return np.zeros(NUM_FEATURES + 3, dtype=np.float32)

        base = np.array([feat[k] for k in FEATURE_NAMES], dtype=np.float32)

        # Position features
        if self.in_position:
            unrealized_pnl = (self.candles[self.current_idx].close / self.entry_price - 1) * 100
            holding_norm = min((self.current_idx - self.entry_idx) / self.max_hold_bars, 1.0)
            pos_feats = np.array([1.0, unrealized_pnl, holding_norm], dtype=np.float32)
        else:
            pos_feats = np.array([0.0, 0.0, 0.0], dtype=np.float32)

        return np.concatenate([base, pos_feats])

    def _get_info(self) -> dict:
        return {
            "idx": self.current_idx,
            "total_pnl": self.total_pnl,
            "trades": len(self.trades),
            "in_position": self.in_position,
        }

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.current_idx = self.start_idx
        self.in_position = False
        self.entry_price = 0.0
        self.entry_idx = 0
        self.total_pnl = 0.0
        self.trades = []
        return self._get_obs(), self._get_info()

    def step(self, action: int):
        reward = 0.0
        price = self.candles[self.current_idx].close

        if action == 1:  # BUY
            if not self.in_position:
                self.in_position = True
                self.entry_price = price
                self.entry_idx = self.current_idx
                reward = -self.commission_pct * 100  # commission cost
            else:
                reward = -0.1  # penalty for invalid buy

        elif action == 2:  # SELL
            if self.in_position:
                pnl_pct = (price / self.entry_price - 1) * 100
                net_pnl = pnl_pct - self.commission_pct * 100 * 2  # entry + exit commission
                reward = net_pnl

                # Bonus for good risk management
                holding = self.current_idx - self.entry_idx
                if net_pnl > 0:
                    reward *= 1.2  # reward profitable trades more
                    if holding <= 5:
                        reward *= 1.1  # bonus for quick profits
                else:
                    if holding >= self.max_hold_bars:
                        reward *= 1.3  # extra penalty for holding losers too long

                self.total_pnl += net_pnl
                self.trades.append({
                    "entry_idx": self.entry_idx,
                    "exit_idx": self.current_idx,
                    "pnl": round(net_pnl, 2),
                })
                self.in_position = False
                self.entry_price = 0.0
            else:
                reward = -0.1  # penalty for invalid sell

        else:  # HOLD
            if self.in_position:
                # Small reward shaping based on position direction
                unrealized = (price / self.entry_price - 1) * 100
                holding = self.current_idx - self.entry_idx

                # Forced exit if held too long
                if holding >= self.max_hold_bars:
                    net_pnl = unrealized - self.commission_pct * 100 * 2
                    reward = net_pnl - 0.5  # penalty for forced exit
                    self.total_pnl += net_pnl
                    self.trades.append({
                        "entry_idx": self.entry_idx,
                        "exit_idx": self.current_idx,
                        "pnl": round(net_pnl, 2),
                        "forced": True,
                    })
                    self.in_position = False
                    self.entry_price = 0.0
                else:
                    # Tiny shaping: reward holding winners, penalize holding losers
                    reward = unrealized * 0.01

        # Advance time
        self.current_idx += 1
        terminated = self.current_idx >= len(self.candles) - 1
        truncated = False

        # Force close position at end
        if terminated and self.in_position:
            close_price = self.candles[self.current_idx].close
            net_pnl = (close_price / self.entry_price - 1) * 100 - self.commission_pct * 100 * 2
            reward += net_pnl
            self.total_pnl += net_pnl
            self.in_position = False

        obs = self._get_obs() if not terminated else np.zeros(NUM_FEATURES + 3, dtype=np.float32)
        return obs, reward, terminated, truncated, self._get_info()


# ─────────────────────────── Multi-Stock Environment ───────────────────────────

class MultiStockTradingEnv(gym.Env):
    """Wraps multiple stocks, randomly picks one each episode for diverse training."""

    metadata = {"render_modes": []}

    def __init__(self, candle_lists: list[list[Candle]], **kwargs):
        self.envs = [TradingEnv(candles, **kwargs) for candles in candle_lists if len(candles) > 100]
        if not self.envs:
            raise ValueError("No valid candle data")
        self.current_env = self.envs[0]
        self.observation_space = self.current_env.observation_space
        self.action_space = self.current_env.action_space

    def reset(self, seed=None, options=None):
        # Randomly pick a stock each episode
        idx = np.random.randint(len(self.envs))
        self.current_env = self.envs[idx]
        return self.current_env.reset(seed=seed, options=options)

    def step(self, action):
        return self.current_env.step(action)


# ─────────────────────────── Training ───────────────────────────

def train_rl(
    candle_lists: list[list[Candle]],
    total_timesteps: int = 100_000,
    model_key: str = "ai_rl",
    progress_cb=None,
) -> dict:
    """Train PPO agent on multi-stock trading environment."""
    from stable_baselines3 import PPO
    from stable_baselines3.common.vec_env import DummyVecEnv

    t0 = _time.time()
    logger.info("Training RL (PPO) agent, timesteps=%d", total_timesteps)

    # Create vectorized environment
    def make_env():
        return MultiStockTradingEnv(candle_lists)

    vec_env = DummyVecEnv([make_env])

    model = PPO(
        "MlpPolicy",
        vec_env,
        learning_rate=3e-4,
        n_steps=2048,
        batch_size=256,
        n_epochs=10,
        gamma=0.99,
        gae_lambda=0.95,
        clip_range=0.2,
        ent_coef=0.01,
        vf_coef=0.5,
        max_grad_norm=0.5,
        verbose=0,
        device="auto",  # will use GPU if available
        policy_kwargs={
            "net_arch": dict(pi=[256, 128], vf=[256, 128]),
        },
    )

    model.learn(total_timesteps=total_timesteps,
                callback=_ProgressCallback(total_timesteps, progress_cb=progress_cb))

    # Save model
    model_path = _MODEL_DIR / f"{model_key}"
    model.save(str(model_path))

    # Evaluate on each stock
    eval_results = []
    for i, candles in enumerate(candle_lists):
        if len(candles) <= 100:
            continue
        env = TradingEnv(candles)
        obs, _ = env.reset()
        while True:
            action, _ = model.predict(obs, deterministic=True)
            obs, reward, terminated, truncated, info = env.step(int(action))
            if terminated or truncated:
                break
        eval_results.append({
            "trades": len(env.trades),
            "total_pnl": round(env.total_pnl, 2),
            "win_trades": sum(1 for t in env.trades if t["pnl"] > 0),
        })

    total_trades = sum(r["trades"] for r in eval_results)
    total_wins = sum(r["win_trades"] for r in eval_results)
    avg_pnl = np.mean([r["total_pnl"] for r in eval_results]) if eval_results else 0

    elapsed = _time.time() - t0
    return {
        "model": "rl_ppo",
        "device": str(model.device),
        "total_timesteps": total_timesteps,
        "stocks_trained": len(eval_results),
        "eval": {
            "total_trades": total_trades,
            "win_trades": total_wins,
            "win_rate": round(total_wins / max(total_trades, 1), 4),
            "avg_pnl_per_stock": round(float(avg_pnl), 2),
            "per_stock": eval_results,
        },
        "elapsed_sec": round(elapsed, 1),
    }


# ─────────────────────────── Prediction ───────────────────────────

def predict_rl(
    candles: list[Candle],
    model_key: str = "ai_rl",
) -> dict | None:
    """Predict next action using trained PPO agent."""
    from stable_baselines3 import PPO

    model_path = _MODEL_DIR / f"{model_key}.zip"
    if not model_path.exists():
        return None

    model = PPO.load(str(model_path), device="auto")

    # Create env for the stock to get proper observation
    env = TradingEnv(candles)
    obs, _ = env.reset()

    # Replay to the end to get current state
    # We simulate: the agent trades through history to build up its position state
    for i in range(env.start_idx, len(candles) - 1):
        action, _ = model.predict(obs, deterministic=True)
        obs, _, terminated, _, _ = env.step(int(action))
        if terminated:
            break

    # Now predict the next action from current state
    action, _ = model.predict(obs, deterministic=True)
    action = int(action)

    # Get action probabilities from the policy
    import torch
    obs_tensor = torch.as_tensor(obs).float().unsqueeze(0).to(model.device)
    with torch.no_grad():
        dist = model.policy.get_distribution(obs_tensor)
        probs = dist.distribution.probs[0].cpu().numpy()

    action_names = ["hold", "buy", "sell"]
    return {
        "hold_prob": round(float(probs[0]), 4),
        "buy_prob": round(float(probs[1]), 4),
        "sell_prob": round(float(probs[2]), 4),
        "action": action_names[action],
        "confidence": round(float(probs[action]) * 100, 1),
        "in_position": env.in_position,
        "total_trades": len(env.trades),
        "session_pnl": round(env.total_pnl, 2),
    }
