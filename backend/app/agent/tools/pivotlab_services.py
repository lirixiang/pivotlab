"""Tools that call pivotlab service functions directly.

Since the agent lives inside the pivotlab backend, we import services
directly — screener, signal generator, backtester, dragon strategy, etc.
No HTTP calls, no API dependency.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

from app.agent.tools.registry import registry

logger = logging.getLogger(__name__)

_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    ".screener_cache",
)


# ── Pattern screener ────────────────────────────────────────────────────

@registry.register(
    name="pl_screener",
    description=(
        "Run pattern screener on the full A-share universe. Patterns: "
        "'breakout_pullback' (突破回踩), 'stabilize' (下跌企稳), "
        "'box_support' (箱体支撑), 'volume_breakout' (放量突破), "
        "'macd_divergence' (MACD底背离). "
        "Returns top matches with score, price, support/resistance, triggers. "
        "Uses cached results if available (< 24h old); otherwise triggers a fresh scan."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "enum": ["breakout_pullback", "stabilize", "box_support",
                         "volume_breakout", "macd_divergence"],
                "description": "Pattern to scan for",
            },
            "min_score": {"type": "number", "default": 60, "description": "Minimum score threshold"},
            "limit": {"type": "integer", "default": 20},
            "force_rescan": {"type": "boolean", "default": False, "description": "Force fresh scan even if cache exists"},
        },
        "required": ["pattern"],
    },
    permission="safe",
)
async def pl_screener(args: dict[str, Any]) -> Any:
    from app.services.screener import PATTERN_DETECTORS

    pattern = args["pattern"]
    min_score = float(args.get("min_score", 60))
    limit = int(args.get("limit", 20))
    force_rescan = bool(args.get("force_rescan", False))

    if pattern not in PATTERN_DETECTORS:
        return {"error": f"unknown pattern '{pattern}', use one of: {list(PATTERN_DETECTORS)}"}

    # Try cached results first (cache valid for 48h — screener runs daily at 15:30)
    if not force_rescan:
        cached = await asyncio.to_thread(_read_cache, pattern, min_score, limit)
        if cached is not None:
            return cached

    # No cache or forced rescan — trigger scan via subprocess
    from app.services.sync_worker import spawn_sync
    started = await asyncio.to_thread(spawn_sync, "screener", pattern=pattern)
    if not started:
        logger.info("screener %s already running, waiting for completion", pattern)

    # Record cache file mtime before scan so we can detect new results
    cache_path = os.path.join(_CACHE_DIR, f"{pattern}.json")
    mtime_before = os.path.getmtime(cache_path) if os.path.exists(cache_path) else 0

    # Poll for completion (scan typically takes 3-8 minutes for ~3000 stocks)
    for i in range(120):  # up to 10 minutes
        await asyncio.sleep(5)
        # Check if cache file was updated
        if os.path.exists(cache_path):
            mtime_now = os.path.getmtime(cache_path)
            if mtime_now > mtime_before:
                cached = await asyncio.to_thread(_read_cache, pattern, min_score, limit, max_age_hours=1)
                if cached is not None:
                    return cached

    return {"status": "timeout", "message": f"{pattern} scan is still running after 10min. Use query_db on scan_results table to check later."}


def _read_cache(pattern: str, min_score: float, limit: int, max_age_hours: float = 48) -> dict | None:
    """Read screener cache JSON. Returns None if missing or too old."""
    path = os.path.join(_CACHE_DIR, f"{pattern}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        scanned_at = data.get("scanned_at")
        if scanned_at:
            ts = datetime.fromisoformat(scanned_at)
            if datetime.now() - ts > timedelta(hours=max_age_hours):
                return None
        items = [it for it in data.get("items", []) if it.get("score", 0) >= min_score]
        items.sort(key=lambda x: x.get("score", 0), reverse=True)
        return {
            "pattern": pattern,
            "scanned_at": scanned_at,
            "total_matches": data.get("total", len(items)),
            "scanned_stocks": data.get("scanned", 0),
            "results": [
                {
                    "code": it.get("code"),
                    "name": it.get("name"),
                    "score": round(it.get("score", 0), 1),
                    "price": round(it.get("price", 0), 2),
                    "change_pct": round(it.get("change_pct", 0), 2),
                    "volume_ratio": round(it.get("volume_ratio", 0), 2),
                    "rr_ratio": round(it.get("rr_ratio") or 0, 2) if it.get("rr_ratio") else None,
                    "support_score": round(it.get("support_score") or 0, 1) if it.get("support_score") else None,
                    "distance_to_support_pct": round(it["distance_to_support_pct"], 2)
                        if it.get("distance_to_support_pct") is not None else None,
                    "triggers": (it.get("triggers") or [])[:3],
                    "industry": it.get("industry", ""),
                    "concept": it.get("concept", ""),
                }
                for it in items[:limit]
            ],
        }
    except Exception as e:
        logger.warning("screener cache read error: %s", e)
        return None


# ── Signal generator ────────────────────────────────────────────────────

@registry.register(
    name="generate_signal",
    description=(
        "Generate a live trading signal for a stock: action (buy/wait/near_signal), "
        "entry price, stop-loss, target prices, confidence (0-100), position sizing. "
        "Uses the multi-factor S/R engine + backtester stats. "
        "Strategies: 'breakout_pullback', 'bottom_stabilize'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "6-digit stock code"},
            "strategy": {
                "type": "string", "default": "breakout_pullback",
                "description": "Strategy: breakout_pullback or bottom_stabilize",
            },
        },
        "required": ["code"],
    },
    permission="safe",
)
async def generate_signal_tool(args: dict[str, Any]) -> Any:
    from app.services.data_provider import get_candles
    from app.services.signal_generator import generate_signal

    code = str(args["code"]).zfill(6)
    strategy = args.get("strategy", "breakout_pullback")

    def _gen():
        candles = get_candles(code, period="daily", days=250)
        if len(candles) < 60:
            return {"error": f"insufficient data for {code} ({len(candles)} bars)"}
        return generate_signal(candles, strategy=strategy)

    return await asyncio.to_thread(_gen)


# ── Backtest ────────────────────────────────────────────────────────────

@registry.register(
    name="run_backtest",
    description=(
        "Run a historical backtest for a stock with a given strategy. "
        "Returns: trade_count, win_rate, avg_return, max_drawdown, profit_factor, "
        "sharpe_ratio, and individual trade list. "
        "Strategies: 'breakout_pullback', 'bottom_stabilize'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "6-digit stock code"},
            "strategy": {"type": "string", "default": "breakout_pullback"},
            "days": {"type": "integer", "default": 500, "description": "Lookback days for backtest"},
        },
        "required": ["code"],
    },
    permission="safe",
)
async def run_backtest_tool(args: dict[str, Any]) -> Any:
    from app.services.data_provider import get_candles
    from app.services.backtester import run_backtest

    code = str(args["code"]).zfill(6)
    strategy = args.get("strategy", "breakout_pullback")
    days = int(args.get("days", 500))

    def _bt():
        candles = get_candles(code, period="daily", days=days)
        if len(candles) < 120:
            return {"error": f"insufficient data for {code}"}
        result = run_backtest(candles, strategy=strategy, period="daily")
        return {
            "code": code,
            "strategy": strategy,
            "bars": len(candles),
            "trade_count": result.trade_count,
            "win_rate": round(result.win_rate * 100, 1),
            "avg_return_pct": round(result.avg_return * 100, 2),
            "max_drawdown_pct": round(result.max_drawdown * 100, 2),
            "profit_factor": round(result.profit_factor, 2),
            "sharpe_ratio": round(result.sharpe_ratio, 2) if result.sharpe_ratio else None,
            "total_return_pct": round(result.total_return * 100, 2),
            "trades": [
                {
                    "entry_date": t.entry_date,
                    "exit_date": t.exit_date,
                    "entry_price": round(t.entry_price, 2),
                    "exit_price": round(t.exit_price, 2),
                    "return_pct": round(t.return_pct * 100, 2),
                    "exit_reason": t.exit_reason,
                }
                for t in (result.trades or [])[-10:]  # last 10 trades
            ],
        }

    return await asyncio.to_thread(_bt)


# ── AI predict (LightGBM) ──────────────────────────────────────────────

@registry.register(
    name="predict_signal",
    description=(
        "Use the trained LightGBM model to predict buy/sell/hold probabilities "
        "for a stock's latest bar. Returns class probabilities and predicted action. "
        "Requires a trained model (train via strategy page first)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "6-digit stock code"},
            "model_key": {"type": "string", "default": "default", "description": "Model key"},
        },
        "required": ["code"],
    },
    permission="safe",
)
async def predict_signal_tool(args: dict[str, Any]) -> Any:
    from app.services.data_provider import get_candles
    from app.services.ai_strategy import predict_lightgbm

    code = str(args["code"]).zfill(6)
    model_key = args.get("model_key", "default")

    def _predict():
        candles = get_candles(code, period="daily", days=250)
        if len(candles) < 60:
            return {"error": f"insufficient data for {code}"}
        result = predict_lightgbm(candles, model_key=model_key)
        if result is None:
            return {"error": f"no trained model found for key '{model_key}'"}
        return {"code": code, **result}

    return await asyncio.to_thread(_predict)


# ── Recommendation lifecycle stats ──────────────────────────────────────

@registry.register(
    name="get_recommendation_stats",
    description=(
        "Get aggregate outcome statistics for AI recommendations: "
        "win rate, avg return, best/worst, by style. "
        "Useful for evaluating recommendation quality."
    ),
    parameters={
        "type": "object",
        "properties": {
            "style": {"type": "string", "description": "Filter by style (short_term/swing/value/multi_factor) or omit for all"},
            "days": {"type": "integer", "default": 90, "description": "Lookback period in days"},
        },
    },
    permission="safe",
)
async def get_recommendation_stats(args: dict[str, Any]) -> Any:
    from app.services.lifecycle import aggregate_outcomes

    style = args.get("style")
    days = int(args.get("days", 90))

    return await asyncio.to_thread(aggregate_outcomes, style=style, days=days)
