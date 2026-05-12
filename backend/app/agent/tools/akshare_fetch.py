"""akshare-based helpers for live market data."""
from __future__ import annotations

import asyncio
from typing import Any

from app.agent.tools.registry import registry


def _to_records(df, limit: int = 50) -> list[dict]:
    if df is None or len(df) == 0:
        return []
    return df.head(limit).to_dict(orient="records")


@registry.register(
    name="get_realtime_quote",
    description="Fetch real-time quote for an A-share stock by code (e.g. 600519, 000001).",
    parameters={
        "type": "object",
        "properties": {"code": {"type": "string", "description": "6-digit stock code"}},
        "required": ["code"],
    },
    permission="safe",
)
async def get_realtime_quote(args: dict[str, Any]) -> dict[str, Any]:
    import akshare as ak

    code = str(args["code"]).zfill(6)

    def _fetch():
        df = ak.stock_zh_a_spot_em()
        row = df[df["代码"] == code]
        return row.to_dict(orient="records")[0] if len(row) else None

    data = await asyncio.to_thread(_fetch)
    if not data:
        return {"error": f"code {code} not found"}
    return data


@registry.register(
    name="get_market_news",
    description="Fetch latest market news headlines (财联社电报).",
    parameters={
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 20}},
    },
    permission="safe",
)
async def get_market_news(args: dict[str, Any]) -> dict[str, Any]:
    import akshare as ak

    limit = int(args.get("limit") or 20)

    def _fetch():
        df = ak.stock_info_global_cls(symbol="全部")
        return _to_records(df, limit)

    return {"news": await asyncio.to_thread(_fetch)}
