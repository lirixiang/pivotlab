"""render_stock_chart: return K-line data + horizontal price lines for the frontend to render."""
from __future__ import annotations

import asyncio
from typing import Any

from app.agent.tools.registry import registry


@registry.register(
    name="render_stock_chart",
    description=(
        "Render a K-line chart for a stock with optional horizontal price lines "
        "(entry, stop-loss, target, etc.). The frontend will display an interactive "
        "candlestick chart with the specified lines drawn. "
        "Use this when recommending a stock to visually show key price levels."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "6-digit stock code"},
            "days": {"type": "integer", "default": 60, "description": "Number of trading days to show"},
            "hlines": {
                "type": "array",
                "description": "Horizontal lines to draw on chart (entry, stop-loss, target prices)",
                "items": {
                    "type": "object",
                    "properties": {
                        "price": {"type": "number", "description": "Price level"},
                        "label": {"type": "string", "description": "Label text, e.g. '入场', '止损', '目标1'"},
                        "color": {
                            "type": "string",
                            "default": "#facc15",
                            "description": "Line color (CSS). Suggested: entry=#facc15, stop=#ef4444, target=#22c55e",
                        },
                        "dash": {"type": "boolean", "default": True, "description": "Dashed line if true"},
                    },
                    "required": ["price", "label"],
                },
            },
            "title": {"type": "string", "description": "Chart title (defaults to stock name)"},
        },
        "required": ["code"],
    },
    permission="safe",
)
async def render_stock_chart(args: dict[str, Any]) -> dict[str, Any]:
    from app.services.data_provider import get_candles

    code = str(args["code"]).zfill(6)
    days = int(args.get("days") or 60)
    hlines = args.get("hlines") or []
    title = args.get("title") or ""

    candles = await asyncio.to_thread(get_candles, code, "daily", days + 10)
    if not candles or len(candles) < 5:
        return {"error": f"no candle data for {code}"}

    candles = candles[-days:]

    # Get stock name for title
    if not title:
        from app.services.data_provider import _get_stock_info_from_db
        name, _ = await asyncio.to_thread(_get_stock_info_from_db, code)
        title = f"{code} {name}" if name else code

    # Normalize hlines
    normalized_hlines = []
    for hl in hlines:
        normalized_hlines.append({
            "price": float(hl["price"]),
            "label": str(hl.get("label", "")),
            "color": str(hl.get("color", "#facc15")),
            "dash": bool(hl.get("dash", True)),
        })

    return {
        "_chart": True,  # signal to frontend: render as chart
        "code": code,
        "title": title,
        "candles": [
            {
                "date": c.date,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
            for c in candles
        ],
        "hlines": normalized_hlines,
    }
