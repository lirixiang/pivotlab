"""P1 — LightGBM-based signal scoring to replace rule-based compute_decision_score.

Workflow:
1. generate_labels()  — use run_backtest results to auto-label candles
2. extract_features() — build feature matrix from raw candle + level data
3. train_model()      — fit LightGBM classifier
4. predict_score()    — output probability as new signal score (0-100)

No manual labelling required — labels are derived from forward returns.
Model is retrained on demand and cached in memory.
"""
from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Any

import numpy as np

from ..schemas import Candle

logger = logging.getLogger(__name__)

_MODEL_CACHE: dict[str, Any] = {}
_MODEL_DIR = Path("/tmp/pivotlab_models")
_MODEL_DIR.mkdir(exist_ok=True)


# ── Feature engineering ──

def extract_features(candles: list[Candle], idx: int) -> dict[str, float] | None:
    """Extract feature vector for bar at *idx*."""
    if idx < 30 or idx >= len(candles):
        return None
    c = candles[idx]
    closes = np.array([candles[j].close for j in range(idx - 29, idx + 1)])
    volumes = np.array([candles[j].volume for j in range(idx - 29, idx + 1)])
    highs = np.array([candles[j].high for j in range(idx - 29, idx + 1)])
    lows = np.array([candles[j].low for j in range(idx - 29, idx + 1)])

    ma5 = float(np.mean(closes[-5:]))
    ma10 = float(np.mean(closes[-10:]))
    ma20 = float(np.mean(closes[-20:]))

    # RSI-14
    diffs = np.diff(closes[-15:])
    gains = np.where(diffs > 0, diffs, 0)
    losses = np.where(diffs < 0, -diffs, 0)
    avg_gain = float(np.mean(gains)) if len(gains) > 0 else 0
    avg_loss = float(np.mean(losses)) if len(losses) > 0 else 1e-9
    rs = avg_gain / max(avg_loss, 1e-9)
    rsi = 100 - 100 / (1 + rs)

    # ATR-14
    trs = []
    for j in range(idx - 13, idx + 1):
        h, lo, pc = candles[j].high, candles[j].low, candles[j - 1].close
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    atr = float(np.mean(trs))

    # Volume ratio
    avg_vol = float(np.mean(volumes[-6:-1])) if np.mean(volumes[-6:-1]) > 0 else 1
    vol_ratio = float(c.volume / avg_vol) if avg_vol > 0 else 1.0

    # Recent return
    ret_5d = float((c.close / closes[-6] - 1) * 100) if closes[-6] > 0 else 0
    ret_10d = float((c.close / closes[-11] - 1) * 100) if closes[-11] > 0 else 0
    ret_20d = float((c.close / closes[0] - 1) * 100) if closes[0] > 0 else 0

    # Bollinger bandwidth
    bb_std = float(np.std(closes[-20:]))
    bb_bw = bb_std / ma20 * 100 if ma20 > 0 else 0

    # Price position in range
    range_30 = float(np.max(highs) - np.min(lows))
    price_pos = (c.close - float(np.min(lows))) / range_30 if range_30 > 0 else 0.5

    # Body ratio (candle body vs range)
    body = abs(c.close - c.open)
    full_range = c.high - c.low
    body_ratio = body / full_range if full_range > 0 else 0

    return {
        "ma5_dist": (c.close - ma5) / ma5 * 100,
        "ma10_dist": (c.close - ma10) / ma10 * 100,
        "ma20_dist": (c.close - ma20) / ma20 * 100,
        "rsi": rsi,
        "atr_pct": atr / c.close * 100,
        "vol_ratio": vol_ratio,
        "ret_5d": ret_5d,
        "ret_10d": ret_10d,
        "ret_20d": ret_20d,
        "bb_bandwidth": bb_bw,
        "price_position": price_pos,
        "body_ratio": body_ratio,
    }


FEATURE_NAMES = [
    "ma5_dist", "ma10_dist", "ma20_dist", "rsi", "atr_pct",
    "vol_ratio", "ret_5d", "ret_10d", "ret_20d",
    "bb_bandwidth", "price_position", "body_ratio",
]


# ── Label generation ──

def generate_dataset(
    candles: list[Candle],
    forward_days: int = 5,
    profit_threshold: float = 3.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Build (X, y) from candles.  y=1 if forward return >= threshold, else 0."""
    X_list: list[list[float]] = []
    y_list: list[int] = []
    for i in range(30, len(candles) - forward_days):
        feat = extract_features(candles, i)
        if feat is None:
            continue
        future_close = candles[i + forward_days].close
        fwd_ret = (future_close / candles[i].close - 1) * 100
        label = 1 if fwd_ret >= profit_threshold else 0
        X_list.append([feat[k] for k in FEATURE_NAMES])
        y_list.append(label)
    return np.array(X_list), np.array(y_list)


# ── Training ──

def train_model(
    candle_lists: list[list[Candle]],
    forward_days: int = 5,
    profit_threshold: float = 3.0,
    model_key: str = "default",
) -> dict:
    """Train LightGBM on multiple stocks' candle data.  Returns training stats."""
    import lightgbm as lgb

    all_X: list[np.ndarray] = []
    all_y: list[np.ndarray] = []
    for candles in candle_lists:
        if len(candles) < 60:
            continue
        X, y = generate_dataset(candles, forward_days, profit_threshold)
        if len(X) > 0:
            all_X.append(X)
            all_y.append(y)

    if not all_X:
        return {"error": "insufficient data"}

    X = np.vstack(all_X)
    y = np.concatenate(all_y)

    pos_count = int(np.sum(y))
    neg_count = len(y) - pos_count
    if pos_count < 10 or neg_count < 10:
        return {"error": "too few positive/negative samples", "pos": pos_count, "neg": neg_count}

    # Train-test split (last 20% as test)
    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    dtrain = lgb.Dataset(X_train, label=y_train, feature_name=FEATURE_NAMES)
    dtest = lgb.Dataset(X_test, label=y_test, reference=dtrain)

    params = {
        "objective": "binary",
        "metric": "auc",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "max_depth": 6,
        "min_child_samples": 20,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "verbose": -1,
    }

    model = lgb.train(
        params, dtrain,
        num_boost_round=200,
        valid_sets=[dtest],
        callbacks=[lgb.early_stopping(20, verbose=False)],
    )

    # Evaluate
    from sklearn.metrics import roc_auc_score, accuracy_score
    y_pred = model.predict(X_test)
    auc = roc_auc_score(y_test, y_pred)
    acc = accuracy_score(y_test, (y_pred >= 0.5).astype(int))

    # Feature importance
    importance = dict(zip(FEATURE_NAMES, model.feature_importance(importance_type="gain").tolist()))

    # Cache model
    _MODEL_CACHE[model_key] = model
    model_path = _MODEL_DIR / f"{model_key}.pkl"
    with open(model_path, "wb") as f:
        pickle.dump(model, f)

    return {
        "samples": len(X),
        "positive_rate": round(pos_count / len(y), 4),
        "auc": round(auc, 4),
        "accuracy": round(acc, 4),
        "feature_importance": {k: round(v, 1) for k, v in sorted(importance.items(), key=lambda x: -x[1])},
    }


# ── Prediction ──

def predict_score(candles: list[Candle], idx: int = -1, model_key: str = "default") -> float | None:
    """Predict signal probability (0-100) for bar at *idx*.
    Returns None if model not trained or features unavailable."""
    if idx < 0:
        idx = len(candles) + idx
    feat = extract_features(candles, idx)
    if feat is None:
        return None

    model = _load_model(model_key)
    if model is None:
        return None

    X = np.array([[feat[k] for k in FEATURE_NAMES]])
    prob = float(model.predict(X)[0])
    return round(prob * 100, 1)


def _load_model(model_key: str):
    if model_key in _MODEL_CACHE:
        return _MODEL_CACHE[model_key]
    model_path = _MODEL_DIR / f"{model_key}.pkl"
    if model_path.exists():
        with open(model_path, "rb") as f:
            model = pickle.load(f)
        _MODEL_CACHE[model_key] = model
        return model
    return None
