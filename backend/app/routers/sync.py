"""Sync management router – trigger sync tasks and check status.

All sync tasks run in separate OS processes via spawn_sync() so they
never block the main FastAPI event loop or its thread pools.
"""
import asyncio

from fastapi import APIRouter, Query

from ..services import sync_service
from ..services.sync_worker import spawn_sync

router = APIRouter(prefix="/api/sync", tags=["sync"])


def _trigger(task_type: str, **kwargs):
    started = spawn_sync(task_type, **kwargs)
    if started:
        return {"task_type": task_type, "status": "started"}
    return {"task_type": task_type, "status": "already_running", "message": "该任务已在运行中"}


@router.post("/stocks")
async def trigger_sync_stocks():
    return _trigger("stocks")


@router.post("/quotes")
async def trigger_sync_quotes():
    return _trigger("quotes")


@router.post("/financials")
async def trigger_sync_financials():
    return _trigger("financials")


@router.post("/concepts")
async def trigger_sync_concepts():
    return _trigger("concepts")


@router.post("/industry")
async def trigger_sync_industry():
    return _trigger("industry")


@router.post("/candles")
async def trigger_sync_candles(days: int = Query(365, ge=30, le=3650)):
    """Start historical daily candles batch sync in a separate process."""
    return _trigger("daily_candles", days=days)


@router.post("/analyst")
async def trigger_sync_analyst():
    return _trigger("analyst_consensus")


@router.get("/tasks")
async def list_sync_tasks():
    return await asyncio.to_thread(sync_service.get_sync_tasks)


@router.get("/db-stats")
async def db_stats():
    return await asyncio.to_thread(sync_service.get_db_stats)
