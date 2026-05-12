"""Tools that call pivotlab services directly (no HTTP needed).

Since the agent now lives inside the pivotlab backend, we can import
and call the services directly — screener, dragon, backtester, etc.
"""
from __future__ import annotations

from typing import Any

from app.agent.tools.registry import registry


@registry.register(
    name="pl_screener",
    description=(
        "Run a pattern screener on local data. Patterns: "
        "'breakout_pullback' (突破回踩), 'stabilize' (下跌企稳), "
        "'box_support' (箱体支撑), 'volume_breakout' (放量突破), "
        "'macd_divergence' (MACD底背离)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "One of: breakout_pullback, stabilize, box_support, volume_breakout, macd_divergence"},
            "limit": {"type": "integer", "default": 20},
        },
        "required": ["pattern"],
    },
    permission="safe",
)
async def pl_screener(args: dict[str, Any]) -> Any:
    from app.services.screener import scan_model
    from app.services.data_provider import get_candles_batch
    from app.database import AsyncSessionLocal
    from sqlalchemy import text

    pattern = args["pattern"]
    limit = int(args.get("limit", 20))

    async with AsyncSessionLocal() as session:
        # Get all active stock codes
        rows = (await session.execute(
            text("SELECT code FROM stocks WHERE is_st = false LIMIT 2000")
        )).fetchall()
        codes = [r[0] for r in rows]

    # Get candles for all codes
    candles_map = await get_candles_batch(codes, days=250)

    results = []
    for code, candles in candles_map.items():
        if len(candles) < 120:
            continue
        try:
            item = scan_model(pattern, code, candles)
            if item and item.total_score >= 60:
                results.append({
                    "code": item.code,
                    "name": item.name,
                    "pattern": pattern,
                    "score": round(item.total_score, 1),
                    "close": item.close,
                    "support": item.nearest_support,
                    "resistance": item.nearest_resistance,
                })
        except Exception:
            continue

    results.sort(key=lambda x: x["score"], reverse=True)
    return {"pattern": pattern, "results": results[:limit], "total_scanned": len(codes)}


@registry.register(
    name="pl_get_market_overview",
    description="Get top-level market overview (indices, breadth, sector flows) from local data.",
    parameters={"type": "object", "properties": {}},
    permission="safe",
)
async def pl_get_market_overview(_args: dict[str, Any]) -> Any:
    from app.services.data_provider import get_market_overview
    return await get_market_overview()
