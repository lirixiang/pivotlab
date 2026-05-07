"""P3 — Hidden Markov Model for market regime detection.

Classifies the market into 2-3 regimes based on return/volatility:
  - Trending (high return, moderate vol)
  - Range-bound (low return, low vol)
  - Crisis (negative return, high vol)

Uses hmmlearn.GaussianHMM on index returns.
No manual labelling needed — regimes are discovered unsupervised.
"""
from __future__ import annotations

import logging
import pickle
from pathlib import Path

import numpy as np

from ..schemas import Candle

logger = logging.getLogger(__name__)

_MODEL_DIR = Path("/tmp/pivotlab_models")
_MODEL_DIR.mkdir(exist_ok=True)

_HMM_CACHE: dict[str, object] = {}

REGIME_NAMES = {0: "趋势", 1: "震荡", 2: "危机"}


def _build_features(candles: list[Candle], window: int = 5) -> np.ndarray:
    """Build (n_samples, 3) feature matrix: [return, volatility, volume_change]."""
    closes = np.array([c.close for c in candles])
    volumes = np.array([c.volume for c in candles])

    # Daily returns
    returns = np.diff(np.log(closes))

    # Rolling volatility (window-day std of returns)
    vol = np.array([
        np.std(returns[max(0, i - window):i]) if i >= window else np.std(returns[:max(i, 1)])
        for i in range(1, len(returns) + 1)
    ])

    # Volume change ratio
    vol_ratio = np.zeros(len(returns))
    for i in range(len(returns)):
        idx = i + 1  # offset by 1 because returns is diff
        if idx >= window and np.mean(volumes[idx - window:idx]) > 0:
            vol_ratio[i] = volumes[idx] / np.mean(volumes[idx - window:idx])
        else:
            vol_ratio[i] = 1.0

    features = np.column_stack([returns, vol, vol_ratio])
    return features


def fit_hmm(
    candles: list[Candle],
    n_regimes: int = 3,
    model_key: str = "hmm_regime",
) -> dict:
    """Fit Gaussian HMM on candle data. Returns regime summary.

    Returns
    -------
    dict with:
      regimes        – list of {name, mean_return, mean_vol, pct} for each regime
      current_regime – current detected regime index and name
      regime_sequence – last 30 bars' regime labels
      transition_matrix – regime transition probabilities
    """
    from hmmlearn.hmm import GaussianHMM

    if len(candles) < 60:
        return {"error": "need at least 60 candles"}

    features = _build_features(candles)

    model = GaussianHMM(
        n_components=n_regimes,
        covariance_type="full",
        n_iter=200,
        random_state=42,
    )
    model.fit(features)
    states = model.predict(features)

    # Reorder states so that highest-return regime = 0 (trending)
    mean_returns = [np.mean(features[states == s, 0]) for s in range(n_regimes)]
    order = np.argsort(mean_returns)[::-1]  # descending by return
    remap = {old: new for new, old in enumerate(order)}
    states = np.array([remap[s] for s in states])

    # Re-fit means after remap for output
    regimes = []
    for i in range(n_regimes):
        mask = states == i
        if not np.any(mask):
            regimes.append({"id": i, "name": REGIME_NAMES.get(i, f"R{i}"),
                            "mean_return": 0, "mean_vol": 0, "pct": 0})
            continue
        regimes.append({
            "id": i,
            "name": REGIME_NAMES.get(i, f"R{i}"),
            "mean_return": round(float(np.mean(features[mask, 0])) * 100, 4),
            "mean_vol": round(float(np.mean(features[mask, 1])) * 100, 4),
            "pct": round(float(np.sum(mask)) / len(states) * 100, 1),
        })

    current = int(states[-1])

    # Transition matrix (from remapped states)
    trans = np.zeros((n_regimes, n_regimes))
    for j in range(1, len(states)):
        trans[states[j - 1], states[j]] += 1
    row_sums = trans.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1
    trans = (trans / row_sums).round(3).tolist()

    # Save model
    _HMM_CACHE[model_key] = (model, remap)
    with open(_MODEL_DIR / f"{model_key}.pkl", "wb") as f:
        pickle.dump((model, remap), f)

    # Regime sequence for last 30 bars
    seq = states[-30:].tolist() if len(states) >= 30 else states.tolist()

    return {
        "regimes": regimes,
        "current_regime": {"id": current, "name": REGIME_NAMES.get(current, f"R{current}")},
        "regime_sequence": seq,
        "transition_matrix": trans,
        "total_bars": len(states),
    }


def predict_regime(
    candles: list[Candle],
    model_key: str = "hmm_regime",
) -> dict | None:
    """Predict current market regime using a previously fitted HMM."""
    loaded = _load_hmm(model_key)
    if loaded is None:
        return None
    model, remap = loaded
    if len(candles) < 10:
        return None
    features = _build_features(candles)
    states = model.predict(features)
    remapped = np.array([remap.get(s, s) for s in states])
    current = int(remapped[-1])
    proba = model.predict_proba(features)[-1]
    # Remap probabilities
    proba_remapped = [round(float(proba[k]), 4) for k in sorted(remap.keys(), key=lambda x: remap[x])]

    return {
        "regime": {"id": current, "name": REGIME_NAMES.get(current, f"R{current}")},
        "probabilities": proba_remapped,
        "recent_sequence": remapped[-10:].tolist(),
    }


def _load_hmm(model_key: str):
    if model_key in _HMM_CACHE:
        return _HMM_CACHE[model_key]
    path = _MODEL_DIR / f"{model_key}.pkl"
    if path.exists():
        with open(path, "rb") as f:
            data = pickle.load(f)
        _HMM_CACHE[model_key] = data
        return data
    return None
