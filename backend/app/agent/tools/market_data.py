"""Market data tools — live quotes & news.

get_realtime_quote: uses tencent_provider (fast, no akshare dependency)
get_market_news: uses akshare (财联社电报, no tencent equivalent)
get_market_overview: index quotes + DB market breadth stats
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import text

from app.agent.tools.registry import registry
from app.database import AsyncSessionLocal


@registry.register(
    name="get_realtime_quote",
    description=(
        "Fetch real-time quote for one or more A-share stocks by code. "
        "Returns: code, name, price, prev_close, open, high, low, volume, amount, "
        "change_amt, change_pct, turnover_rate. "
        "Supports multiple codes separated by comma (e.g. '600519,000001')."
    ),
    parameters={
        "type": "object",
        "properties": {
            "codes": {
                "type": "string",
                "description": "One or more 6-digit codes, comma-separated",
            },
        },
        "required": ["codes"],
    },
    permission="safe",
)
async def get_realtime_quote(args: dict[str, Any]) -> dict[str, Any]:
    from app.services.tencent_provider import fetch_quotes

    raw = str(args["codes"]).replace(" ", "")
    codes = [c.strip().zfill(6) for c in raw.split(",") if c.strip()]
    if not codes:
        return {"error": "no valid codes"}

    quotes = await asyncio.to_thread(fetch_quotes, codes)
    if not quotes:
        return {"error": f"codes {','.join(codes)} not found or market closed"}
    from datetime import datetime as _dt
    return {
        "quotes": quotes,
        "count": len(quotes),
        "_meta": {
            "source": "tencent_realtime",
            "fetched_at": _dt.now().isoformat(timespec="seconds"),
            "note": "盘后返回的是收盘价快照",
        },
    }


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
        if df is None or len(df) == 0:
            return []
        return df.head(limit).to_dict(orient="records")

    return {"news": await asyncio.to_thread(_fetch)}


@registry.register(
    name="get_market_overview",
    description=(
        "Get market overview: index quotes (上证/深证/创业板), "
        "market breadth (涨跌家数, 涨停跌停), sector flow top-N."
    ),
    parameters={"type": "object", "properties": {}},
    permission="safe",
)
async def get_market_overview(_args: dict[str, Any]) -> dict[str, Any]:
    from app.services.tencent_provider import fetch_index_quotes

    # Index quotes (sync → thread)
    indices = await asyncio.to_thread(fetch_index_quotes)

    # Market breadth from DB
    async with AsyncSessionLocal() as session:
        # Latest trade date
        r = await session.execute(text("SELECT MAX(trade_date) FROM daily_candles"))
        latest = r.scalar()
        if not latest:
            return {"indices": indices, "latest_date": None}

        # Up/down count
        r = await session.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE change_pct > 0) as up_count,
                COUNT(*) FILTER (WHERE change_pct < 0) as down_count,
                COUNT(*) FILTER (WHERE change_pct = 0) as flat_count,
                COUNT(*) FILTER (WHERE change_pct >= 9.9) as zt_count,
                COUNT(*) FILTER (WHERE change_pct <= -9.9) as dt_count,
                ROUND(AVG(change_pct)::numeric, 2) as avg_pct
            FROM daily_candles d JOIN stocks s USING (code)
            WHERE d.trade_date = :dt AND s.is_st = false
        """), {"dt": latest})
        breadth = dict(r.mappings().first() or {})

        # Sector flow top 5
        r = await session.execute(text("""
            SELECT concept, change_pct_1d, net_inflow
            FROM concept_boards
            WHERE net_inflow IS NOT NULL
            ORDER BY net_inflow DESC LIMIT 5
        """))
        top_inflow = [dict(row) for row in r.mappings().all()]

        r = await session.execute(text("""
            SELECT concept, change_pct_1d, net_inflow
            FROM concept_boards
            WHERE net_inflow IS NOT NULL
            ORDER BY net_inflow ASC LIMIT 5
        """))
        top_outflow = [dict(row) for row in r.mappings().all()]

    return {
        "latest_date": latest,
        "indices": indices,
        "breadth": breadth,
        "top_inflow_sectors": top_inflow,
        "top_outflow_sectors": top_outflow,
    }
