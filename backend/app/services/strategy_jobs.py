"""Strategy / ML jobs runnable from the unified sync-task spawner.

Each function:
  - accepts an optional ``_task_id`` kwarg (assigned by the spawner)
  - updates the SyncTask row to ``done`` / ``error`` on completion
  - logs to stdout (captured by ``.sync_<task>.log``)
"""
from __future__ import annotations

import logging
from typing import Any

from .sync_service import _finish_task

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
def _wrap(task_id: int | None, fn, **kwargs):
    try:
        result = fn(**kwargs)
        n = 0
        if isinstance(result, dict):
            n = sum(int(v) for v in result.values() if isinstance(v, (int, float)))
        if task_id is not None:
            _finish_task(task_id, n, n, "")
        logger.info("job done: %s", result)
        return result
    except Exception as e:
        logger.exception("job failed")
        if task_id is not None:
            _finish_task(task_id, 0, 0, str(e))
        raise


# ── Daily recommendation scan ────────────────────────────────
def run_recommend_scan(
    *,
    _task_id: int | None = None,
    styles: list[str] | None = None,
    top_n: int = 100,
    min_score: float = 50.0,
    universe_limit: int | None = None,
):
    from ..strategy.recommender import scan_universe

    def go():
        return scan_universe(
            styles=styles, top_n=top_n, min_score=min_score,
            universe_limit=universe_limit,
        )

    return _wrap(_task_id, go)


# ── ML training jobs ─────────────────────────────────────────
def run_train_lgbm(
    *,
    _task_id: int | None = None,
    horizon_days: int = 10,
    universe_limit: int = 600,
    history_years: float = 2.0,
):
    from ..strategy.ml import lgbm

    def go():
        meta = lgbm.train(
            horizon_days=horizon_days,
            universe_limit=universe_limit,
            history_years=history_years,
        )
        return {"trained_lgbm": 1, **{k: v for k, v in meta.items() if isinstance(v, (int, float))}}

    return _wrap(_task_id, go)


def run_train_seq(
    *,
    _task_id: int | None = None,
    horizon_days: int = 10,
    universe_limit: int = 600,
    history_years: float = 2.0,
    epochs: int = 12,
):
    from ..strategy.ml import sequence

    def go():
        meta = sequence.train(
            horizon_days=horizon_days,
            universe_limit=universe_limit,
            history_years=history_years,
            epochs=epochs,
        )
        return {"trained_seq": 1, **{k: v for k, v in meta.items() if isinstance(v, (int, float))}}

    return _wrap(_task_id, go)


def run_train_rl(
    *,
    _task_id: int | None = None,
    universe_limit: int = 200,
    total_timesteps: int = 80_000,
):
    from ..strategy.ml import rl_position

    def go():
        meta = rl_position.train(
            universe_limit=universe_limit,
            total_timesteps=total_timesteps,
        )
        return {"trained_rl": 1, **{k: v for k, v in meta.items() if isinstance(v, (int, float))}}

    return _wrap(_task_id, go)


# ── Index k-line sync ────────────────────────────────────────
def run_sync_indices(*, _task_id: int | None = None):
    from . import index_sync

    def go():
        return index_sync.sync_indices()

    return _wrap(_task_id, go)


# ── Recommendation lifecycle update ──────────────────────────
def run_lifecycle_update(*, _task_id: int | None = None, lookback_days: int = 60):
    from . import lifecycle

    def go():
        result = lifecycle.update_lifecycle(lookback_days=lookback_days)
        # Flatten state counts so _wrap can total
        flat = {"processed": result.get("n_processed", 0)}
        for k, v in (result.get("states") or {}).items():
            flat[f"state_{k}"] = v
        return flat

    return _wrap(_task_id, go)
