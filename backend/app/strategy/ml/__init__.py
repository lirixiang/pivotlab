"""Machine-learning extensions on top of the v1 rule-based strategy layer.

Modules
-------
dataset      Build leakage-free historical training samples from `daily_candles`.
lgbm         LightGBM LambdaRank trainer + predictor (cross-sectional ranker).
sequence     Tiny TCN/Transformer trainer + predictor on 60-bar OHLCV windows.
rl_position  Gymnasium env + Stable-Baselines3 PPO for position-sizing decisions.
ensemble     Blends rule + LGBM + sequence scores into the `ai_ensemble` style.
registry     Save/load model artifacts to backend/data/models/.

The ML modules are *additive* — if no model file is found, callers get the
graceful fallback (rule-based score). This means the rest of the platform
keeps working unchanged.
"""

from . import dataset, ensemble, lgbm, registry, sequence, rl_position  # noqa: F401

__all__ = [
    "dataset",
    "ensemble",
    "lgbm",
    "registry",
    "sequence",
    "rl_position",
]
