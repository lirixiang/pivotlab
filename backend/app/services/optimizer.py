"""P0 — Optuna-based parameter auto-optimizer for the backtest engine.

Wraps *run_backtest* and searches for the BacktestConfig parameter set
that maximises a composite objective (Sharpe, return, drawdown).

No training / model artefacts needed — pure black-box optimisation.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Literal

import optuna

from ..schemas import Candle
from .backtester import BacktestConfig, run_backtest, Strategy

logger = logging.getLogger(__name__)

# Silence Optuna's verbose logging
optuna.logging.set_verbosity(optuna.logging.WARNING)

ObjectiveTarget = Literal["sharpe", "return", "calmar"]


def _objective(
    trial: optuna.Trial,
    candles: list[Candle],
    strategy: Strategy,
    period: str,
    target: ObjectiveTarget,
) -> float:
    """Single trial: suggest params → run backtest → return metric."""
    cfg = BacktestConfig(
        stop_loss_pct=trial.suggest_float("stop_loss_pct", 1.0, 8.0, step=0.5),
        target_pct=trial.suggest_float("target_pct", 3.0, 15.0, step=0.5),
        max_hold_bars=trial.suggest_int("max_hold_bars", 5, 40, step=5),
        use_atr_stop=trial.suggest_categorical("use_atr_stop", [True, False]),
        atr_stop_mult=trial.suggest_float("atr_stop_mult", 1.0, 4.0, step=0.5),
        volume_filter=trial.suggest_categorical("volume_filter", [True, False]),
        shrink_filter=trial.suggest_categorical("shrink_filter", [True, False]),
        close_above_support=True,
        weekly_confluence=True,
        ma_trend_filter=trial.suggest_categorical("ma_trend_filter", [True, False]),
        ma_trend_period=trial.suggest_int("ma_trend_period", 10, 60, step=5),
        pullback_min_pct=trial.suggest_float("pullback_min_pct", 0.05, 1.0, step=0.05),
        pullback_max_pct=trial.suggest_float("pullback_max_pct", 1.5, 6.0, step=0.5),
        min_level_score=trial.suggest_int("min_level_score", 10, 60, step=10),
        stabilize_bars=trial.suggest_int("stabilize_bars", 2, 6),
        stabilize_max_dist_pct=trial.suggest_float("stabilize_max_dist_pct", 1.0, 6.0, step=0.5),
        commission_pct=0.1,
        slippage_pct=0.05,
        cooldown_bars=trial.suggest_int("cooldown_bars", 0, 5),
    )
    result = run_backtest(candles, strategy=strategy, period=period, config=cfg)
    stats = result.stats

    if stats["total_trades"] < 3:
        return -999.0  # too few trades → penalise

    if target == "sharpe":
        return stats["sharpe"]
    elif target == "return":
        return stats["total_return"]
    else:  # calmar
        dd = abs(stats["max_drawdown"])
        return stats["total_return"] / dd if dd > 0 else stats["total_return"]


def optimise_params(
    candles: list[Candle],
    strategy: Strategy = "breakout_pullback",
    period: str = "3m",
    n_trials: int = 80,
    target: ObjectiveTarget = "sharpe",
) -> dict:
    """Run Optuna optimisation and return best params + comparison stats.

    Returns
    -------
    dict with keys:
      best_params  – optimal BacktestConfig fields
      best_value   – objective value achieved
      default_value – objective with default params
      best_stats   – full stats dict for best params
      default_stats – full stats dict for default params
      trials_count – number of trials run
    """
    study = optuna.create_study(direction="maximize")
    study.optimize(
        lambda trial: _objective(trial, candles, strategy, period, target),
        n_trials=n_trials,
        show_progress_bar=False,
    )

    best = study.best_params
    best_cfg = BacktestConfig(**{k: v for k, v in best.items() if hasattr(BacktestConfig, k)})
    best_result = run_backtest(candles, strategy=strategy, period=period, config=best_cfg)

    default_result = run_backtest(candles, strategy=strategy, period=period, config=BacktestConfig())

    def _metric(stats: dict) -> float:
        if target == "sharpe":
            return stats["sharpe"]
        elif target == "return":
            return stats["total_return"]
        else:
            dd = abs(stats["max_drawdown"])
            return stats["total_return"] / dd if dd > 0 else stats["total_return"]

    return {
        "best_params": asdict(best_cfg),
        "best_value": round(_metric(best_result.stats), 4),
        "default_value": round(_metric(default_result.stats), 4),
        "best_stats": best_result.stats,
        "default_stats": default_result.stats,
        "trials_count": len(study.trials),
    }
