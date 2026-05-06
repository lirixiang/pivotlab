"""Sync management router – trigger sync tasks and check status.

All sync tasks run in separate OS processes via spawn_sync() so they
never block the main FastAPI event loop or its thread pools.
"""
import asyncio

from fastapi import APIRouter, Query

from ..services import sync_service
from ..services.sync_worker import spawn_sync

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("/stocks")
async def trigger_sync_stocks():
    spawn_sync("stocks")
    return {"task_type": "stocks", "status": "started"}


@router.post("/quotes")
async def trigger_sync_quotes():
    spawn_sync("quotes")
    return {"task_type": "quotes", "status": "started"}


@router.post("/financials")
async def trigger_sync_financials():
    spawn_sync("financials")
    return {"task_type": "financials", "status": "started"}


@router.post("/concepts")
async def trigger_sync_concepts():
    spawn_sync("concepts")
    return {"task_type": "concepts", "status": "started"}


@router.post("/industry")
async def trigger_sync_industry():
    spawn_sync("industry")
    return {"task_type": "industry", "status": "started"}


@router.post("/candles")
async def trigger_sync_candles(days: int = Query(365, ge=30, le=3650)):
    """Start historical daily candles batch sync in a separate process."""
    spawn_sync("daily_candles", days=days)
    return {"task_type": "daily_candles", "status": "started", "days": days}


@router.post("/analyst")
async def trigger_sync_analyst():
    spawn_sync("analyst_consensus")
    return {"task_type": "analyst_consensus", "status": "started"}


@router.get("/tasks")
async def list_sync_tasks():
    return await asyncio.to_thread(sync_service.get_sync_tasks)


@router.get("/db-stats")
async def db_stats():
    return await asyncio.to_thread(sync_service.get_db_stats)
