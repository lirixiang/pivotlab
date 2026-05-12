"""Data sync tools — let the agent trigger data syncing when DB data is missing/stale.

Uses spawn_sync() which runs sync tasks in separate OS processes.
The tool polls sync_tasks table to wait for completion before returning results.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from sqlalchemy import text

from app.agent.tools.registry import registry
from app.database import AsyncSessionLocal


async def _poll_task(task_id: int, timeout: float = 300) -> dict:
    """Poll sync_tasks until finished or timeout."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        async with AsyncSessionLocal() as session:
            row = (await session.execute(
                text("SELECT status, total, processed, error_msg FROM sync_tasks WHERE id = :id"),
                {"id": task_id},
            )).first()
        if not row:
            return {"status": "error", "error": "task not found"}
        status, total, processed, error_msg = row
        if status in ("done", "error"):
            result: dict = {"status": status, "total": total or 0, "processed": processed or 0}
            if error_msg:
                result["error"] = error_msg
            return result
        await asyncio.sleep(3)
    return {"status": "timeout", "message": f"sync still running after {timeout}s, check later"}


async def _run_sync(task_type: str, **kwargs) -> dict[str, Any]:
    """Spawn a sync task and wait for completion."""
    from app.services.sync_worker import spawn_sync
    from app.services.sync_service import _get_session

    # Get the task_id that spawn_sync creates
    started = await asyncio.to_thread(spawn_sync, task_type, **kwargs)
    if not started:
        return {"status": "skipped", "message": f"{task_type} is already running"}

    # Find the task_id (latest running task of this type)
    def _find_task_id():
        with _get_session() as s:
            row = s.execute(text(
                "SELECT id FROM sync_tasks WHERE task_type = :t AND status = 'running' "
                "ORDER BY id DESC LIMIT 1"
            ), {"t": task_type}).first()
            return row[0] if row else None

    task_id = await asyncio.to_thread(_find_task_id)
    if not task_id:
        return {"status": "started", "message": f"{task_type} started but task_id not found"}

    return await _poll_task(task_id)


@registry.register(
    name="sync_stock_list",
    description=(
        "Sync the A-share stock list (code, name, market, is_st) from EastMoney. "
        "Run this when stocks table seems empty or new IPO stocks are missing."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
    permission="confirm",
    summarize=lambda _: "Sync stock list from EastMoney",
)
async def sync_stock_list(args: dict[str, Any]) -> dict[str, Any]:
    return await _run_sync("stocks")


@registry.register(
    name="sync_quotes",
    description=(
        "Sync real-time quotes for all stocks into daily_candles (today's row). "
        "Run this when today's daily_candles data is missing or you need the latest intraday snapshot."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
    permission="confirm",
    summarize=lambda _: "Sync real-time quotes for all stocks",
)
async def sync_quotes(args: dict[str, Any]) -> dict[str, Any]:
    return await _run_sync("quotes")


@registry.register(
    name="sync_daily_candles",
    description=(
        "Sync historical daily K-lines for all stocks. "
        "Run this when daily_candles table has no history or a stock is missing candle data. "
        "Default syncs last 365 days. Phase 1 batch-fetches today; Phase 2 backfills gaps."
    ),
    parameters={
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "Number of days of history to sync (default 365)",
            },
        },
        "required": [],
    },
    permission="confirm",
    summarize=lambda a: f"Sync daily candles (last {a.get('days', 365)} days)",
)
async def sync_daily_candles(args: dict[str, Any]) -> dict[str, Any]:
    days = int(args.get("days", 365))
    return await _run_sync("daily_candles", days=days)


@registry.register(
    name="sync_financials",
    description=(
        "Sync financial snapshots (EPS, ROE, revenue/profit YoY, PE ratio) for all stocks. "
        "Run this when financial_snapshots data is missing or stale."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
    permission="confirm",
    summarize=lambda _: "Sync financial data for all stocks",
)
async def sync_financials(args: dict[str, Any]) -> dict[str, Any]:
    return await _run_sync("financials")


@registry.register(
    name="sync_concepts",
    description=(
        "Sync stock-to-concept mapping and concept board data. "
        "Run this when stock_concepts or concept_boards is empty."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
    permission="confirm",
    summarize=lambda _: "Sync concept boards and stock-concept mapping",
)
async def sync_concepts(args: dict[str, Any]) -> dict[str, Any]:
    return await _run_sync("concepts")


@registry.register(
    name="sync_zt_pool",
    description=(
        "Sync 涨停池/炸板池 data for a given date. "
        "Run this when zt_pool_daily is missing data for today or a specific date."
    ),
    parameters={
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "Date in YYYYMMDD format (default: today)",
            },
        },
        "required": [],
    },
    permission="confirm",
    summarize=lambda a: f"Sync 涨停池 ({a.get('date', 'today')})",
)
async def sync_zt_pool(args: dict[str, Any]) -> dict[str, Any]:
    kwargs = {}
    if args.get("date"):
        kwargs["date_str"] = args["date"]
    return await _run_sync("zt_pool", **kwargs)


@registry.register(
    name="sync_lhb",
    description=(
        "Sync 龙虎榜 records and seat details for a given date. "
        "Run this when lhb_records or lhb_seat_details is missing data."
    ),
    parameters={
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "Date in YYYYMMDD format (default: today)",
            },
        },
        "required": [],
    },
    permission="confirm",
    summarize=lambda a: f"Sync 龙虎榜 ({a.get('date', 'today')})",
)
async def sync_lhb(args: dict[str, Any]) -> dict[str, Any]:
    kwargs = {}
    if args.get("date"):
        kwargs["date_str"] = args["date"]
    return await _run_sync("lhb", **kwargs)


@registry.register(
    name="sync_concept_heat",
    description=(
        "Sync concept heat history (板块热度: heat_score, heat_level, leader info) for a date. "
        "Run this when concept_heat_history is missing data."
    ),
    parameters={
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "Date in YYYYMMDD format (default: today)",
            },
        },
        "required": [],
    },
    permission="confirm",
    summarize=lambda a: f"Sync concept heat ({a.get('date', 'today')})",
)
async def sync_concept_heat(args: dict[str, Any]) -> dict[str, Any]:
    kwargs = {}
    if args.get("date"):
        kwargs["date_str"] = args["date"]
    return await _run_sync("concept_heat_history", **kwargs)


@registry.register(
    name="sync_indices",
    description=(
        "Sync index K-lines (上证/深成/创业板/沪深300/中证500) from Tencent. "
        "Run this when index_candles data is missing."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
    permission="confirm",
    summarize=lambda _: "Sync index K-lines",
)
async def sync_indices(args: dict[str, Any]) -> dict[str, Any]:
    return await _run_sync("sync_indices")


@registry.register(
    name="sync_analyst",
    description=(
        "Sync analyst consensus data (target prices, ratings, EPS forecasts). "
        "Run this when analyst_consensus table is empty or stale."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
    permission="confirm",
    summarize=lambda _: "Sync analyst consensus data",
)
async def sync_analyst(args: dict[str, Any]) -> dict[str, Any]:
    return await _run_sync("analyst_consensus")


@registry.register(
    name="check_sync_status",
    description=(
        "Check data freshness: latest trade_date in key tables and recent sync task status. "
        "Use this to decide which data needs syncing."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
    permission="safe",
)
async def check_sync_status(args: dict[str, Any]) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        # Check latest dates in key tables
        checks = {
            "daily_candles": "SELECT MAX(trade_date) FROM daily_candles",
            "zt_pool_daily": "SELECT MAX(trade_date) FROM zt_pool_daily",
            "lhb_records": "SELECT MAX(trade_date::varchar) FROM lhb_records",
            "concept_heat_history": "SELECT MAX(trade_date::varchar) FROM concept_heat_history",
            "index_candles": "SELECT MAX(trade_date) FROM index_candles",
            "stocks": "SELECT COUNT(*) FROM stocks",
            "stock_concepts": "SELECT COUNT(*) FROM stock_concepts",
            "financial_snapshots": "SELECT COUNT(*) FROM financial_snapshots",
            "analyst_consensus": "SELECT COUNT(*) FROM analyst_consensus",
        }
        freshness = {}
        for table, sql in checks.items():
            try:
                row = (await session.execute(text(sql))).scalar()
                freshness[table] = str(row) if row else "empty"
            except Exception:
                freshness[table] = "error"

        # Recent sync tasks
        rows = (await session.execute(text(
            "SELECT task_type, status, total, processed, error_msg, started_at "
            "FROM sync_tasks ORDER BY id DESC LIMIT 10"
        ))).fetchall()
        recent_tasks = [
            {
                "type": rows[i][0], "status": rows[i][1],
                "total": rows[i][2], "processed": rows[i][3],
                "error": rows[i][4], "started": str(rows[i][5]) if rows[i][5] else None,
            }
            for i in range(len(rows))
        ]

    return {"data_freshness": freshness, "recent_sync_tasks": recent_tasks}
