"""Reinforcement Learning trading strategy using PPO.

Unlike supervised models that learn from labeled buy/sell points,
the RL agent learns by interacting with a simulated market environment,
maximizing cumulative profit through trial and error.

Architecture:
  - TradingEnv (gymnasium.Env): simulates trading with real candle data
  - PPO (stable-baselines3): policy gradient agent with clipped objective
  - State: 37 technical features + 10 SR features + 3 position = 50 dims
  - Actions: 0=hold, 1=buy, 2=sell
  - Reward: realized PnL on exit, with SR-aware shaping for risk management
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
from .levels_multifactor import detect_levels_multifactor

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

# Extra SR features beyond the 5 already in FEATURE_NAMES
_SR_EXTRA_FEATURES = 10  # s2_dist, r2_dist, s1_touches, r1_touches, n_supports_nearby,
                          # n_resistances_nearby, at_support, at_resistance, breakout_up, breakout_down
_TOTAL_OBS = NUM_FEATURES + _SR_EXTRA_FEATURES + 3  # 37 + 10 + 3 = 50

_SR_PROXIMITY_PCT = 1.0  # within 1% counts as "at" level
_SR_NEARBY_PCT = 3.0     # within 3% counts as "nearby"


class TradingEnv(gym.Env):
    """Gym environment that simulates stock trading with SR-aware state.

    State (50 dims):
      - 37 technical features (includes basic sr_dist_support etc.)
      - 10 extra SR features:
          s2_dist: distance to 2nd support (%)
          r2_dist: distance to 2nd resistance (%)
          s1_touches: touch count of nearest support (normalized)
          r1_touches: touch count of nearest resistance (normalized)
          n_supports_nearby: number of supports within 3%
          n_resistances_nearby: number of resistances within 3%
          at_support: 1 if within 1% of support, else 0
          at_resistance: 1 if within 1% of resistance, else 0
          breakout_up: 1 if price just broke above resistance
          breakout_down: 1 if price just broke below support
      - 3 position features (in_position, unrealized_pnl, holding_norm)

    Actions: 0=hold, 1=buy, 2=sell

    Rewards:
      - Base: realized PnL on trade exit
      - SR bonus: +0.3 for buying near support, +0.3 for selling near resistance
      - SR penalty: -0.3 for buying into resistance, -0.3 for selling at support
      - Breakout bonus: +0.5 for buying on breakout above resistance
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        candles: list[Candle],
        commission_pct: float = 0.15,
        max_hold_bars: int = 20,
        start_idx: int = 60,
        precomputed_sr: dict[int, list] | None = None,
    ):
        super().__init__()
        self.candles = candles
        self.commission_pct = commission_pct / 100
        self.max_hold_bars = max_hold_bars
        self.start_idx = start_idx

        # State: 37 base + 10 SR extra + 3 position
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf,
            shape=(_TOTAL_OBS,), dtype=np.float32,
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

        # Pre-computed SR levels (keyed by cache_key = idx // 20 * 20)
        self._sr_levels_cache = precomputed_sr if precomputed_sr is not None else {}
        self._sr_refresh = 20

    def _get_sr_levels(self, idx: int) -> list:
        """Get SR levels at idx from pre-computed cache or compute on-the-fly."""
        cache_key = (idx // self._sr_refresh) * self._sr_refresh
        if cache_key not in self._sr_levels_cache:
            subset = self.candles[:idx + 1]
            lookback = min(len(subset), 120)
            try:
                levels = detect_levels_multifactor(subset, lookback=lookback)
            except Exception:
                levels = []
            self._sr_levels_cache[cache_key] = levels
        return self._sr_levels_cache[cache_key]

    def _get_sr_extra_features(self, idx: int) -> np.ndarray:
        """Compute 10 extra SR features for the observation."""
        price = self.candles[idx].close
        levels = self._get_sr_levels(idx)

        supports = sorted([lv for lv in levels if lv.kind == "support"],
                          key=lambda lv: abs(lv.price - price))
        resistances = sorted([lv for lv in levels if lv.kind == "resistance"],
                             key=lambda lv: abs(lv.price - price))

        # S2 distance (2nd nearest support)
        s2_dist = 0.0
        if len(supports) >= 2:
            s2_dist = (price - supports[1].price) / price * 100

        # R2 distance (2nd nearest resistance)
        r2_dist = 0.0
        if len(resistances) >= 2:
            r2_dist = (resistances[1].price - price) / price * 100

        # Touch counts (normalized by max 10)
        s1_touches = supports[0].touches / 10.0 if supports else 0
        r1_touches = resistances[0].touches / 10.0 if resistances else 0

        # Nearby counts
        n_supports_nearby = sum(
            1 for lv in supports
            if abs(lv.price - price) / price * 100 < _SR_NEARBY_PCT
        )
        n_resistances_nearby = sum(
            1 for lv in resistances
            if abs(lv.price - price) / price * 100 < _SR_NEARBY_PCT
        )

        # At level flags
        at_support = 0.0
        if supports:
            nearest_s_dist = (price - supports[0].price) / price * 100
            if abs(nearest_s_dist) < _SR_PROXIMITY_PCT:
                at_support = 1.0

        at_resistance = 0.0
        if resistances:
            nearest_r_dist = (resistances[0].price - price) / price * 100
            if abs(nearest_r_dist) < _SR_PROXIMITY_PCT:
                at_resistance = 1.0

        # Breakout detection (compare with previous bar)
        breakout_up = 0.0
        breakout_down = 0.0
        if idx > 0:
            prev_price = self.candles[idx - 1].close
            if resistances and prev_price < resistances[0].price <= price:
                breakout_up = 1.0
            if supports and prev_price > supports[0].price >= price:
                breakout_down = 1.0

        return np.array([
            s2_dist, r2_dist, s1_touches, r1_touches,
            n_supports_nearby, n_resistances_nearby,
            at_support, at_resistance, breakout_up, breakout_down,
        ], dtype=np.float32)

    def _get_sr_reward_shaping(self, action: int, idx: int) -> float:
        """Compute SR-aware reward bonus/penalty."""
        price = self.candles[idx].close
        levels = self._get_sr_levels(idx)

        supports = [lv for lv in levels if lv.kind == "support"]
        resistances = [lv for lv in levels if lv.kind == "resistance"]

        nearest_s_dist = float('inf')
        nearest_r_dist = float('inf')
        if supports:
            nearest_s = min(supports, key=lambda lv: abs(lv.price - price))
            nearest_s_dist = (price - nearest_s.price) / price * 100

        if resistances:
            nearest_r = min(resistances, key=lambda lv: abs(lv.price - price))
            nearest_r_dist = (nearest_r.price - price) / price * 100

        bonus = 0.0

        if action == 1:  # BUY
            # Bonus for buying near support (within 1.5%)
            if 0 <= nearest_s_dist < 1.5:
                bonus += 0.3 * (1.0 - nearest_s_dist / 1.5)
            # Penalty for buying right at resistance (within 1%)
            if 0 <= nearest_r_dist < 1.0:
                bonus -= 0.3
            # Breakout bonus
            if idx > 0:
                prev_price = self.candles[idx - 1].close
                if resistances:
                    nearest_r = min(resistances, key=lambda lv: abs(lv.price - price))
                    if prev_price < nearest_r.price <= price:
                        bonus += 0.5  # breakout buy

        elif action == 2:  # SELL
            # Bonus for selling near resistance (within 1.5%)
            if 0 <= nearest_r_dist < 1.5:
                bonus += 0.3 * (1.0 - nearest_r_dist / 1.5)
            # Penalty for selling at support (giving up the bounce)
            if 0 <= nearest_s_dist < 1.0:
                bonus -= 0.2

        return bonus

    def _get_obs(self) -> np.ndarray:
        feat = extract_features(self.candles, self.current_idx)
        if feat is None:
            return np.zeros(_TOTAL_OBS, dtype=np.float32)

        base = np.array([feat[k] for k in FEATURE_NAMES], dtype=np.float32)

        # Extra SR features
        sr_extra = self._get_sr_extra_features(self.current_idx)

        # Position features
        if self.in_position:
            unrealized_pnl = (self.candles[self.current_idx].close / self.entry_price - 1) * 100
            holding_norm = min((self.current_idx - self.entry_idx) / self.max_hold_bars, 1.0)
            pos_feats = np.array([1.0, unrealized_pnl, holding_norm], dtype=np.float32)
        else:
            pos_feats = np.array([0.0, 0.0, 0.0], dtype=np.float32)

        return np.concatenate([base, sr_extra, pos_feats])

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

        # SR-aware reward shaping for buy/sell actions
        sr_bonus = self._get_sr_reward_shaping(action, self.current_idx)

        if action == 1:  # BUY
            if not self.in_position:
                self.in_position = True
                self.entry_price = price
                self.entry_idx = self.current_idx
                reward = -self.commission_pct * 100 + sr_bonus
            else:
                reward = -0.1  # penalty for invalid buy

        elif action == 2:  # SELL
            if self.in_position:
                pnl_pct = (price / self.entry_price - 1) * 100
                net_pnl = pnl_pct - self.commission_pct * 100 * 2  # entry + exit commission
                reward = net_pnl + sr_bonus

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

        obs = self._get_obs() if not terminated else np.zeros(_TOTAL_OBS, dtype=np.float32)
        return obs, reward, terminated, truncated, self._get_info()


# ─────────────────────────── Multi-Stock Environment ───────────────────────────


def _precompute_sr_levels(candles: list[Candle], refresh: int = 20) -> dict[int, list]:
    """Pre-compute SR levels for all cache keys in a candle series."""
    sr_cache: dict[int, list] = {}
    start_idx = 60
    for idx in range(start_idx, len(candles), refresh):
        cache_key = (idx // refresh) * refresh
        if cache_key in sr_cache:
            continue
        subset = candles[:idx + 1]
        lookback = min(len(subset), 120)
        try:
            levels = detect_levels_multifactor(subset, lookback=lookback)
        except Exception:
            levels = []
        sr_cache[cache_key] = levels
    return sr_cache


class MultiStockTradingEnv(gym.Env):
    """Wraps multiple stocks, randomly picks one each episode for diverse training."""

    metadata = {"render_modes": []}

    def __init__(self, candle_lists: list[list[Candle]], sr_caches: list[dict] | None = None, **kwargs):
        valid = [(i, c) for i, c in enumerate(candle_lists) if len(c) > 100]
        if not valid:
            raise ValueError("No valid candle data")

        self.envs = []
        for i, candles in valid:
            sr = sr_caches[i] if sr_caches else None
            self.envs.append(TradingEnv(candles, precomputed_sr=sr, **kwargs))

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
    """Train PPO agent on multi-stock trading environment.

    Supports incremental training: if a model already exists, loads and continues.
    Saves checkpoint every 10% of progress.
    """
    from stable_baselines3 import PPO
    from stable_baselines3.common.vec_env import DummyVecEnv

    t0 = _time.time()
    logger.info("Training RL (PPO) agent, timesteps=%d", total_timesteps)

    # Pre-compute SR levels for all stocks (avoids heavy computation during training)
    logger.info("Pre-computing SR levels for %d stocks...", len(candle_lists))
    sr_caches = []
    for i, candles in enumerate(candle_lists):
        if len(candles) > 100:
            sr_caches.append(_precompute_sr_levels(candles))
        else:
            sr_caches.append({})
    logger.info("SR pre-computation done.")

    # Create vectorized environment
    def make_env():
        return MultiStockTradingEnv(candle_lists, sr_caches=sr_caches)

    vec_env = DummyVecEnv([make_env])

    model_path = _MODEL_DIR / f"{model_key}"
    existing_model = _MODEL_DIR / f"{model_key}.zip"

    # Load existing model for continued training
    if existing_model.exists():
        logger.info("Loading existing model for continued training: %s", existing_model)
        try:
            model = PPO.load(str(model_path), env=vec_env, device="cpu")
            model.learning_rate = 3e-4
        except Exception as e:
            logger.warning("Failed to load existing model (%s), training from scratch", e)
            model = PPO(
                "MlpPolicy",
                vec_env,
                learning_rate=3e-4,
                n_steps=1024,
                batch_size=256,
                n_epochs=10,
                gamma=0.99,
                gae_lambda=0.95,
                clip_range=0.2,
                ent_coef=0.01,
                vf_coef=0.5,
                max_grad_norm=0.5,
                verbose=0,
                device="cpu",
                policy_kwargs={
                    "net_arch": dict(pi=[256, 128], vf=[256, 128]),
                },
            )
    else:
        model = PPO(
            "MlpPolicy",
            vec_env,
            learning_rate=3e-4,
            n_steps=1024,
            batch_size=256,
            n_epochs=10,
            gamma=0.99,
            gae_lambda=0.95,
            clip_range=0.2,
            ent_coef=0.01,
            vf_coef=0.5,
            max_grad_norm=0.5,
            verbose=0,
            device="cpu",  # MlpPolicy runs better on CPU
            policy_kwargs={
                "net_arch": dict(pi=[256, 128], vf=[256, 128]),
            },
        )

    # Checkpoint callback: save every 5%
    class _CheckpointCB(BaseCallback):
        def __init__(self, save_path, save_freq):
            super().__init__()
            self.save_path = save_path
            self.save_freq = save_freq

        def _on_step(self) -> bool:
            if self.num_timesteps % self.save_freq == 0:
                self.model.save(str(self.save_path))
            return True

    save_freq = max(total_timesteps // 20, 1024)  # Save every 5%
    callbacks = [
        _ProgressCallback(total_timesteps, progress_cb=progress_cb),
        _CheckpointCB(model_path, save_freq),
    ]

    model.learn(total_timesteps=total_timesteps, callback=callbacks)

    # Save final model
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
