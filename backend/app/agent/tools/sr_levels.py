"""calc_sr_levels: compute support/resistance using the multi-factor engine.

Calls data_provider.get_candles (sync, with DB cache) and
levels_multifactor.detect_levels_multifactor — no raw SQL needed.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.agent.tools.registry import registry


@registry.register(
    name="calc_sr_levels",
    description=(
        "Compute support and resistance levels for a stock using the multi-factor S/R engine. "
        "Returns scored levels with strength (1-5), confidence score (0-100), touch count, "
        "and distance to current price. Uses daily + weekly confluence, false-break validation."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "6-digit stock code"},
            "lookback_days": {"type": "integer", "default": 120, "description": "Window size in trading days"},
        },
        "required": ["code"],
    },
    permission="safe",
)
async def calc_sr_levels(args: dict[str, Any]) -> dict[str, Any]:
    from app.services.data_provider import get_candles
    from app.services.levels_multifactor import detect_levels_multifactor

    code = str(args["code"]).zfill(6)
    lookback = int(args.get("lookback_days") or 120)

    def _compute():
        candles = get_candles(code, period="daily", days=lookback + 30)
        if not candles or len(candles) < 30:
            return None, None, None

        levels = detect_levels_multifactor(candles, lookback=lookback)
        last_close = candles[-1].close
        return candles, levels, last_close

    candles, levels, last_close = await asyncio.to_thread(_compute)
    if candles is None:
        return {"error": f"no candle data for {code}"}

    supports = []
    resistances = []
    for lv in levels:
        entry = {
            "price": round(lv.price, 2),
            "strength": lv.strength,
            "score": round(lv.score, 1),
            "touches": lv.touches,
            "distance_pct": round((lv.price - last_close) / last_close * 100, 2),
            "note": lv.note,
        }
        if lv.kind == "support" and lv.price < last_close:
            supports.append(entry)
        elif lv.kind == "resistance" and lv.price > last_close:
            resistances.append(entry)

    supports.sort(key=lambda x: -x["price"])   # closest first
    resistances.sort(key=lambda x: x["price"])  # closest first

    last_bar_date = candles[-1].date if candles else None
    return {
        "code": code,
        "last_close": round(last_close, 2),
        "lookback_days": lookback,
        "supports": supports[:5],
        "resistances": resistances[:5],
        "_meta": {
            "based_on_bar_date": last_bar_date,
            "bars_used": len(candles),
            "note": "如 last_bar_date 不是今天，结果反映的是历史状态",
        },
    }
