"""Reinforcement learning for *position management* (not stock picking).

Setting
-------
Given a stock that has already been *picked* by the upstream model, an RL
agent learns when to scale-in / scale-out / exit.  The state is the recent
OHLCV window plus current PnL and time-in-trade; the action is one of
{HOLD, ADD, TRIM, EXIT}; the reward is realized PnL minus a small holding
cost.

This is the niche where RL actually has an edge over ML — the decision is
sequential and path-dependent, and the agent learns timing rather than
selection.

Public API
----------
train(...)                -> dict     # PPO fit + save
suggest_position_mult(window, *, score) -> float  # 0.5 .. 1.5 multiplier
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ...database import DATABASE_URL
from ...models import DailyCandle, Stock
from . import registry

logger = logging.getLogger(__name__)

NAME = "rl_ppo"


def _model_path() -> Path:
    return registry.model_dir(NAME) / "ppo.zip"


# ──────────────────────────────────────────────────────────────
#  Gym environment
# ──────────────────────────────────────────────────────────────
def _make_env_class():
    import gymnasium as gym
    from gymnasium import spaces

    SEQ_LEN = 30          # last-30-bar window  (compact state)
    MAX_HOLD = 30         # episode length cap

    class TradingEnv(gym.Env):
        """Single-stock single-position environment.

        State (flat float32):  norm-OHLCV (30*5) + [position, pnl_pct, days_held/MAX_HOLD]
        Action: 0=HOLD 1=ADD 2=TRIM 3=EXIT
        """
        metadata = {"render_modes": []}

        def __init__(self, episodes: list[np.ndarray]):
            super().__init__()
            self.episodes = episodes  # list of (T,5) candle arrays
            self.action_space = spaces.Discrete(4)
            self.observation_space = spaces.Box(
                low=-10, high=10,
                shape=(SEQ_LEN * 5 + 3,),
                dtype=np.float32,
            )
            self._rng = np.random.default_rng(0)
            self.cur_ep = None
            self.t = 0
            self.position = 0.0
            self.entry_price = 0.0
            self.days_held = 0

        def _state(self):
            ep = self.cur_ep
            i0 = max(0, self.t - SEQ_LEN + 1)
            window = ep[i0: self.t + 1]
            if len(window) < SEQ_LEN:
                pad = np.tile(window[0:1], (SEQ_LEN - len(window), 1))
                window = np.concatenate([pad, window], axis=0)
            base = window[0, 3] if window[0, 3] > 0 else 1.0
            ohlc = np.log(np.clip(window[:, 0:4] / base, 1e-6, None)).astype(np.float32)
            v = window[:, 4]
            v_norm = ((v - v.mean()) / (v.std() + 1e-6)).astype(np.float32)
            seq_flat = np.concatenate([ohlc.flatten(), v_norm])
            cur_price = ep[self.t, 3]
            pnl = (cur_price / max(self.entry_price, 1e-6) - 1.0) if self.position > 0 else 0.0
            extras = np.array([
                self.position,
                np.clip(pnl, -1.0, 1.0),
                self.days_held / MAX_HOLD,
            ], dtype=np.float32)
            return np.concatenate([seq_flat, extras]).astype(np.float32)

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            if seed is not None:
                self._rng = np.random.default_rng(seed)
            ep_idx = int(self._rng.integers(0, len(self.episodes)))
            ep = self.episodes[ep_idx]
            # Random start so we have ≥ SEQ_LEN history and ≥ MAX_HOLD future
            T = ep.shape[0]
            if T <= SEQ_LEN + MAX_HOLD + 2:
                self.cur_ep = ep
                self.t = SEQ_LEN
            else:
                start = int(self._rng.integers(SEQ_LEN, T - MAX_HOLD - 1))
                self.cur_ep = ep
                self.t = start
            self.position = 0.0
            self.entry_price = 0.0
            self.days_held = 0
            return self._state(), {}

        def step(self, action):
            ep = self.cur_ep
            cur_price = float(ep[self.t, 3])
            prev_position = self.position
            reward = 0.0

            if action == 1:  # ADD
                if self.position < 1.0:
                    new_pos = min(1.0, self.position + 0.5)
                    new_entry = ((self.entry_price * self.position) + (cur_price * (new_pos - self.position))) / max(new_pos, 1e-6)
                    self.position = new_pos
                    self.entry_price = new_entry
                else:
                    reward -= 0.001  # noop penalty
            elif action == 2:  # TRIM
                if self.position > 0:
                    realized = (cur_price / max(self.entry_price, 1e-6) - 1.0) * (self.position * 0.5)
                    reward += realized
                    self.position *= 0.5
                else:
                    reward -= 0.001
            elif action == 3:  # EXIT
                if self.position > 0:
                    realized = (cur_price / max(self.entry_price, 1e-6) - 1.0) * self.position
                    reward += realized
                    self.position = 0.0
                    self.entry_price = 0.0

            # advance one bar
            self.t += 1
            self.days_held += 1 if self.position > 0 else 0

            # carry-cost: tiny daily slippage to discourage churn
            if prev_position != self.position:
                reward -= 0.0005  # transaction cost
            # mark-to-market shaping reward (smoothed)
            if self.position > 0 and self.t < ep.shape[0]:
                next_price = float(ep[self.t, 3])
                reward += (next_price / cur_price - 1.0) * self.position * 0.1

            done = (self.days_held >= MAX_HOLD) or (self.t >= ep.shape[0] - 1)
            if done and self.position > 0:
                last_price = float(ep[self.t, 3])
                reward += (last_price / max(self.entry_price, 1e-6) - 1.0) * self.position
                self.position = 0.0

            return self._state(), float(reward), bool(done), False, {}

    return TradingEnv, SEQ_LEN, MAX_HOLD


# ──────────────────────────────────────────────────────────────
def _load_episodes(universe_limit: int = 200, history_days: int = 500) -> list[np.ndarray]:
    eng = create_engine(
        str(DATABASE_URL).replace("sqlite+aiosqlite", "sqlite")
                          .replace("postgresql+asyncpg", "postgresql+psycopg2"),
        echo=False, pool_pre_ping=True,
    )
    cutoff = (date.today() - timedelta(days=history_days * 2)).strftime("%Y-%m-%d")
    with Session(eng) as session:
        codes = [s.code for s in session.execute(
            select(Stock).where(Stock.is_st == False)  # noqa: E712
        ).scalars().all()][:universe_limit]
        rows = session.execute(
            select(DailyCandle).where(
                DailyCandle.code.in_(codes),
                DailyCandle.trade_date >= cutoff,
            ).order_by(DailyCandle.code, DailyCandle.trade_date.asc())
        ).scalars().all()
    by_code: dict[str, list] = defaultdict(list)
    for r in rows:
        by_code[r.code].append(r)
    eps = []
    for code, cl in by_code.items():
        if len(cl) < 80:
            continue
        arr = np.array(
            [[c.open or 0, c.high or 0, c.low or 0, c.close or 0, c.volume or 0] for c in cl],
            dtype=np.float32,
        )
        eps.append(arr)
    return eps


# ──────────────────────────────────────────────────────────────
def train(
    *,
    total_timesteps: int = 80_000,
    universe_limit: int = 200,
    progress_cb=None,
) -> dict:
    from stable_baselines3 import PPO
    from stable_baselines3.common.vec_env import DummyVecEnv

    if progress_cb:
        progress_cb({"phase": "load_episodes", "pct": 5})
    episodes = _load_episodes(universe_limit=universe_limit)
    if not episodes:
        raise RuntimeError("rl: no episodes loaded")
    logger.info("rl: %d episodes", len(episodes))

    if progress_cb:
        progress_cb({"phase": "init_env", "pct": 15, "episodes": len(episodes)})

    EnvCls, _seq_len, _max_hold = _make_env_class()
    venv = DummyVecEnv([lambda: EnvCls(episodes) for _ in range(4)])

    model = PPO(
        "MlpPolicy", venv,
        learning_rate=3e-4, n_steps=512, batch_size=128,
        gamma=0.99, gae_lambda=0.95, ent_coef=0.01,
        verbose=0,
    )

    if progress_cb:
        progress_cb({"phase": "ppo_train", "pct": 25, "total_timesteps": total_timesteps})

    # Periodic progress callback hooked via SB3 callback
    from stable_baselines3.common.callbacks import BaseCallback

    class _Prog(BaseCallback):
        def __init__(self, total):
            super().__init__()
            self.total = total
            self._last = 0
        def _on_step(self) -> bool:
            cur = self.num_timesteps
            if cur - self._last > self.total // 10:
                self._last = cur
                if progress_cb:
                    progress_cb({"phase": "ppo_train",
                                 "pct": 25 + int(70 * cur / self.total),
                                 "timesteps": int(cur)})
            return True

    model.learn(total_timesteps=total_timesteps, callback=_Prog(total_timesteps))
    model.save(str(_model_path()))

    # Quick eval: average reward over 100 episodes
    eval_env = EnvCls(episodes)
    rewards = []
    for _ in range(100):
        obs, _ = eval_env.reset()
        done = False
        ep_r = 0.0
        while not done:
            action, _ = model.predict(obs, deterministic=True)
            obs, r, done, _, _ = eval_env.step(int(action))
            ep_r += r
        rewards.append(ep_r)
    avg_r = float(np.mean(rewards))

    meta = {
        "model": NAME,
        "total_timesteps": total_timesteps,
        "episodes": len(episodes),
        "eval_avg_reward": round(avg_r, 4),
    }
    registry.write_meta(NAME, meta)
    logger.info("rl trained: %s", meta)

    if progress_cb:
        progress_cb({"phase": "done", "pct": 100, **meta})

    global _ppo
    _ppo = None
    return meta


# ──────────────────────────────────────────────────────────────
#  Inference
# ──────────────────────────────────────────────────────────────
_ppo = None


def is_trained() -> bool:
    return _model_path().exists()


def _load() -> bool:
    global _ppo
    if _ppo is not None:
        return True
    p = _model_path()
    if not p.exists():
        return False
    from stable_baselines3 import PPO
    _ppo = PPO.load(str(p))
    return True


def suggest_position_mult(window: np.ndarray, *, score: float) -> float:
    """Convert the agent's preference into a position-size multiplier in
    [0.5, 1.5].  We measure the agent's confidence by how much probability
    mass it puts on ADD vs EXIT after seeing this window.

    `window` is (T, 5) raw OHLCV; T should be >= 30.
    Returns 1.0 if the model is not loaded.
    """
    if not _load() or window.shape[0] < 30 or window.shape[1] != 5:
        return 1.0
    EnvCls, SEQ_LEN, _ = _make_env_class()
    base = window[-SEQ_LEN:, 3:4][0, 0] if window[-SEQ_LEN, 3] > 0 else 1.0
    ohlc = np.log(np.clip(window[-SEQ_LEN:, 0:4] / base, 1e-6, None)).astype(np.float32)
    v = window[-SEQ_LEN:, 4]
    v_norm = ((v - v.mean()) / (v.std() + 1e-6)).astype(np.float32)
    extras = np.array([0.0, 0.0, 0.0], dtype=np.float32)  # no current position
    obs = np.concatenate([ohlc.flatten(), v_norm, extras]).astype(np.float32)

    import torch
    obs_t = torch.from_numpy(obs).unsqueeze(0).to(_ppo.device)
    with torch.no_grad():
        dist = _ppo.policy.get_distribution(obs_t)
        probs = dist.distribution.probs.cpu().numpy()[0]   # [HOLD, ADD, TRIM, EXIT]
    bullish = float(probs[1])      # ADD
    bearish = float(probs[2] + probs[3])  # TRIM + EXIT
    edge = bullish - bearish       # in [-1, 1]
    mult = 1.0 + edge * 0.5
    return float(np.clip(mult, 0.5, 1.5))
