"""P2 — RL-based dynamic position sizing (PPO via stable-baselines3).

Environment: a stock trading environment where the agent decides how much
to allocate (discrete: 0%, 25%, 50%, 75%, 100%) at each bar.

Observation space = technical feature vector (same as ml_scorer features
plus current position info).

Reward = risk-adjusted daily PnL (penalises large drawdowns and frequent trading).

Usage:
  train_rl_agent(candle_lists)  → trains and saves a PPO model
  rl_position_size(candles, idx) → returns suggested allocation 0.0-1.0
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from ..schemas import Candle
from .ml_scorer import extract_features, FEATURE_NAMES

logger = logging.getLogger(__name__)

_MODEL_DIR = Path("/tmp/pivotlab_models")
_MODEL_DIR.mkdir(exist_ok=True)

_RL_CACHE: dict[str, Any] = {}

# ── Gym Environment ──

try:
    import gymnasium as gym
    from gymnasium import spaces
    HAS_GYM = True
except ImportError:
    import gym  # type: ignore
    from gym import spaces  # type: ignore
    HAS_GYM = True


class TradingEnv(gym.Env):
    """Single-stock position sizing environment."""

    metadata = {"render_modes": []}
    ACTIONS = [0.0, 0.25, 0.5, 0.75, 1.0]  # allocation levels

    def __init__(self, candles: list[Candle], commission: float = 0.001):
        super().__init__()
        self.candles = candles
        self.commission = commission
        # obs = 12 technical features + 2 position features (alloc, unrealised_pnl)
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(14,), dtype=np.float32)
        self.action_space = spaces.Discrete(len(self.ACTIONS))
        self._start_idx = 30
        self._idx = self._start_idx
        self._alloc = 0.0
        self._entry_price = 0.0
        self._equity = 1.0
        self._prev_equity = 1.0
        self._peak = 1.0
        self._trade_count = 0

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self._idx = self._start_idx
        self._alloc = 0.0
        self._entry_price = 0.0
        self._equity = 1.0
        self._prev_equity = 1.0
        self._peak = 1.0
        self._trade_count = 0
        return self._obs(), {}

    def _obs(self) -> np.ndarray:
        feat = extract_features(self.candles, self._idx)
        if feat is None:
            tech = np.zeros(len(FEATURE_NAMES), dtype=np.float32)
        else:
            tech = np.array([feat[k] for k in FEATURE_NAMES], dtype=np.float32)
        pos_info = np.array([
            self._alloc,
            (self.candles[self._idx].close / max(self._entry_price, 1e-9) - 1) * 100
            if self._alloc > 0 else 0.0
        ], dtype=np.float32)
        return np.concatenate([tech, pos_info])

    def step(self, action: int):
        new_alloc = self.ACTIONS[action]
        bar = self.candles[self._idx]
        price = bar.close

        # If allocation changed, incur commission on the delta
        delta = abs(new_alloc - self._alloc)
        if delta > 0.01:
            self._equity *= (1 - delta * self.commission)
            self._trade_count += 1
            if new_alloc > 0 and self._alloc == 0:
                self._entry_price = price

        # Price change from this bar to next
        self._idx += 1
        done = self._idx >= len(self.candles) - 1
        if not done:
            next_price = self.candles[self._idx].close
            ret = (next_price / price - 1)
            self._equity *= (1 + new_alloc * ret)
        self._alloc = new_alloc
        if self._alloc == 0:
            self._entry_price = 0.0

        # Track peak for drawdown
        if self._equity > self._peak:
            self._peak = self._equity
        dd = (self._equity - self._peak) / self._peak

        # Reward: daily equity change with drawdown penalty
        daily_ret = (self._equity / self._prev_equity - 1) if self._prev_equity > 0 else 0
        reward = daily_ret * 100  # scale up
        reward += dd * 10  # penalise drawdown (dd is negative)
        # Penalise excessive trading
        if delta > 0.01:
            reward -= 0.05

        self._prev_equity = self._equity
        obs = self._obs() if not done else np.zeros(14, dtype=np.float32)
        return obs, float(reward), done, False, {"equity": self._equity}


# ── Training ──

def train_rl_agent(
    candle_lists: list[list[Candle]],
    total_timesteps: int = 50000,
    model_key: str = "rl_ppo",
) -> dict:
    """Train PPO agent on multiple stocks. Returns training stats."""
    from stable_baselines3 import PPO
    from stable_baselines3.common.vec_env import DummyVecEnv

    # Use the longest candle list for training
    valid = [c for c in candle_lists if len(c) >= 100]
    if not valid:
        return {"error": "insufficient candle data (need >= 100 bars)"}

    # Create environment from the combined longest series
    longest = max(valid, key=len)

    def make_env():
        return TradingEnv(longest)

    env = DummyVecEnv([make_env])

    model = PPO(
        "MlpPolicy", env,
        learning_rate=3e-4,
        n_steps=256,
        batch_size=64,
        n_epochs=10,
        gamma=0.99,
        verbose=0,
    )
    model.learn(total_timesteps=total_timesteps)

    # Save
    model_path = _MODEL_DIR / f"{model_key}"
    model.save(str(model_path))
    _RL_CACHE[model_key] = model

    # Evaluate: run one episode
    eval_env = TradingEnv(longest)
    obs, _ = eval_env.reset()
    total_reward = 0.0
    steps = 0
    while True:
        action, _ = model.predict(obs, deterministic=True)
        obs, reward, done, _, info = eval_env.step(int(action))
        total_reward += reward
        steps += 1
        if done:
            break

    return {
        "total_timesteps": total_timesteps,
        "eval_total_reward": round(total_reward, 2),
        "eval_final_equity": round(info["equity"], 4),
        "eval_trades": eval_env._trade_count,
        "eval_steps": steps,
    }


# ── Inference ──

def rl_position_size(
    candles: list[Candle],
    idx: int = -1,
    model_key: str = "rl_ppo",
) -> dict | None:
    """Get RL-suggested position allocation for bar at *idx*.
    Returns {"allocation": 0.0-1.0, "action": int} or None."""
    model = _load_rl_model(model_key)
    if model is None:
        return None
    if idx < 0:
        idx = len(candles) + idx
    feat = extract_features(candles, idx)
    if feat is None:
        return None
    tech = np.array([feat[k] for k in FEATURE_NAMES], dtype=np.float32)
    pos_info = np.array([0.0, 0.0], dtype=np.float32)  # no current position context
    obs = np.concatenate([tech, pos_info])
    action, _ = model.predict(obs, deterministic=True)
    alloc = TradingEnv.ACTIONS[int(action)]
    return {"allocation": alloc, "action": int(action)}


def _load_rl_model(model_key: str):
    if model_key in _RL_CACHE:
        return _RL_CACHE[model_key]
    model_path = _MODEL_DIR / f"{model_key}.zip"
    if model_path.exists():
        from stable_baselines3 import PPO
        model = PPO.load(str(model_path.with_suffix("")))
        _RL_CACHE[model_key] = model
        return model
    return None
