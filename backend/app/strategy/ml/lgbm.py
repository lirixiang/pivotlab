"""LightGBM cross-sectional ranker.

Use LambdaRank (objective='lambdarank') with one query group per snapshot
date.  Predicts a continuous score; we then rescale to 0-100 for UI.

Public API
----------
train(...) -> dict             # train + save model artifact
predict(features: FeatureSet) -> float | None
batch_predict(rows: np.ndarray) -> np.ndarray
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from . import dataset as ds
from . import registry

logger = logging.getLogger(__name__)

NAME = "lgbm_ranker"


def _model_path() -> Path:
    return registry.model_dir(NAME) / "model.txt"


# ──────────────────────────────────────────────────────────────
def train(
    *,
    horizon_days: int = 10,
    snapshot_step: int = 5,
    history_years: float = 2.0,
    universe_limit: int | None = 600,
    num_leaves: int = 63,
    learning_rate: float = 0.05,
    n_estimators: int = 400,
    progress_cb=None,
) -> dict:
    import lightgbm as lgb

    if progress_cb:
        progress_cb({"phase": "build_dataset", "pct": 5})

    data = ds.build_dataset(
        horizon_days=horizon_days,
        snapshot_step=snapshot_step,
        history_years=history_years,
        universe_limit=universe_limit,
        progress_cb=progress_cb,
    )
    train_set, val_set = data.split_time(val_frac=0.2)
    if len(train_set.groups) == 0 or len(val_set.groups) == 0:
        raise RuntimeError("lgbm.train: empty split")

    if progress_cb:
        progress_cb({"phase": "fit_lgbm", "pct": 75,
                     "train_samples": int(train_set.X.shape[0]),
                     "val_samples": int(val_set.X.shape[0])})

    # LambdaRank requires integer relevance labels. Bucket the percentile
    # rank into 5 levels (0=worst, 4=best).
    def _rel(y_rank: np.ndarray) -> np.ndarray:
        return np.clip((y_rank * 5).astype(int), 0, 4)

    train_lgb = lgb.Dataset(
        train_set.X, label=_rel(train_set.y_rank),
        group=train_set.groups, free_raw_data=False,
    )
    val_lgb = lgb.Dataset(
        val_set.X, label=_rel(val_set.y_rank),
        group=val_set.groups, free_raw_data=False,
        reference=train_lgb,
    )

    params = {
        "objective": "lambdarank",
        "metric": ["ndcg"],
        "ndcg_eval_at": [10, 20],
        "boosting_type": "gbdt",
        "num_leaves": num_leaves,
        "learning_rate": learning_rate,
        "feature_fraction": 0.85,
        "bagging_fraction": 0.85,
        "bagging_freq": 5,
        "min_data_in_leaf": 20,
        "verbose": -1,
    }

    booster = lgb.train(
        params,
        train_lgb,
        num_boost_round=n_estimators,
        valid_sets=[val_lgb],
        valid_names=["val"],
        callbacks=[lgb.early_stopping(stopping_rounds=30, verbose=False),
                   lgb.log_evaluation(period=0)],
    )

    out_path = _model_path()
    booster.save_model(str(out_path))

    # Quick rank-IC on validation set (Spearman of predicted score vs forward return)
    val_pred = booster.predict(val_set.X)
    val_ic = _spearman(val_pred, val_set.y_ret)

    importances = dict(zip(
        ds.FEATURE_COLS,
        [int(x) for x in booster.feature_importance(importance_type="gain")],
    ))
    top_imp = sorted(importances.items(), key=lambda kv: -kv[1])[:10]

    meta = {
        "model": NAME,
        "horizon_days": horizon_days,
        "history_years": history_years,
        "samples_train": int(train_set.X.shape[0]),
        "samples_val": int(val_set.X.shape[0]),
        "groups_train": int(len(train_set.groups)),
        "groups_val": int(len(val_set.groups)),
        "best_iteration": int(booster.best_iteration or 0),
        "val_rank_ic": round(float(val_ic), 4),
        "top_features": top_imp,
    }
    registry.write_meta(NAME, meta)
    logger.info("lgbm trained: %s", meta)

    if progress_cb:
        progress_cb({"phase": "done", "pct": 100, **meta})

    # Reset cached predictor
    global _booster, _bounds
    _booster = None
    _bounds = None
    return meta


# ──────────────────────────────────────────────────────────────
_booster = None
_bounds: tuple[float, float] | None = None  # (min,max) of training pred for scaling


def _load() -> bool:
    global _booster
    if _booster is not None:
        return True
    p = _model_path()
    if not p.exists():
        return False
    import lightgbm as lgb
    _booster = lgb.Booster(model_file=str(p))
    return True


def is_trained() -> bool:
    return _model_path().exists()


def batch_predict(X: np.ndarray) -> np.ndarray | None:
    """Raw LambdaRank score (higher = better). Returns None if no model."""
    if not _load():
        return None
    return _booster.predict(X)


def predict_score_0_100(features) -> float | None:
    """Return a 0-100 calibrated score for one FeatureSet, or None."""
    if not _load():
        return None
    row = ds.features_to_row(features).reshape(1, -1)
    raw = float(_booster.predict(row)[0])
    return _calibrate(raw)


def _calibrate(raw: float) -> float:
    """Rough sigmoid calibration: maps raw lambdarank score to ~0-100."""
    # LambdaRank scores are roughly mean-zero; scale by ~3.
    s = 1.0 / (1.0 + np.exp(-raw / 3.0))
    return float(np.clip(s * 100.0, 0.0, 100.0))


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2:
        return 0.0
    ra = np.argsort(np.argsort(a))
    rb = np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])
