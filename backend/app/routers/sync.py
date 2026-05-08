"""Sync management router – trigger sync tasks and check status.

All sync tasks run in separate OS processes via spawn_sync() so they
never block the main FastAPI event loop or its thread pools.
"""
import asyncio

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..services import sync_service
from ..services.sync_worker import spawn_sync
from ..services.source_registry import get_catalog_with_selection, save_selected_sources

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


@router.post("/financial_history")
async def trigger_sync_financial_history(years: int = Query(5, ge=1, le=10)):
    """Sync historical quarterly financial reports (default 5 years)."""
    return _trigger("financial_history", years=years)


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


# ── Schedule config ──

class ScheduleItem(BaseModel):
    enabled: bool
    cron: str

class SchedulePayload(BaseModel):
    schedules: dict[str, ScheduleItem]


@router.get("/schedule")
async def get_schedule():
    """Return current schedule config (merged defaults + user overrides)."""
    from ..main import _load_schedule_config, _scheduler
    config = _load_schedule_config()
    # Add next_run info from live scheduler
    result = {}
    for k, v in config.items():
        item = {**v}
        if _scheduler:
            job = _scheduler.get_job(f"sched_{k}")
            if job and job.next_run_time:
                item["next_run"] = job.next_run_time.strftime("%Y-%m-%d %H:%M")
        result[k] = item
    return result


@router.put("/schedule")
async def put_schedule(payload: SchedulePayload):
    """Save schedule config and reload the scheduler."""
    from ..main import _scheduler, _apply_schedule, DEFAULT_SCHEDULE
    from ..database import get_db
    from ..models import UserSettings
    from sqlalchemy import select
    from datetime import datetime

    # Validate cron expressions
    from apscheduler.triggers.cron import CronTrigger
    for task_type, item in payload.schedules.items():
        if task_type not in DEFAULT_SCHEDULE:
            return {"ok": False, "error": f"未知任务类型: {task_type}"}
        if item.enabled:
            try:
                CronTrigger.from_crontab(item.cron)
            except Exception:
                return {"ok": False, "error": f"无效 cron 表达式: {item.cron}"}

    # Build config to save (only save enabled/cron, not label/desc)
    save_data = {}
    for task_type, item in payload.schedules.items():
        save_data[task_type] = {"enabled": item.enabled, "cron": item.cron}

    # Save to DB
    async for db in get_db():
        row = (await db.execute(
            select(UserSettings).where(UserSettings.key == "schedule_config")
        )).scalar_one_or_none()
        if row:
            row.value = save_data
            row.updated_at = datetime.utcnow()
        else:
            db.add(UserSettings(key="schedule_config", value=save_data))
        await db.commit()
        break

    # Hot-reload scheduler
    if _scheduler:
        merged = {}
        for k, v in DEFAULT_SCHEDULE.items():
            merged[k] = {**v, **save_data.get(k, {})}
        _apply_schedule(_scheduler, merged)

    return {"ok": True}


# ── Data source config ──

@router.get("/sources")
async def get_sources():
    """Return available data sources per task type with current selections."""
    return await asyncio.to_thread(get_catalog_with_selection)


class SourceUpdate(BaseModel):
    sources: dict[str, str]  # task_type -> source_id


@router.put("/sources")
async def put_sources(payload: SourceUpdate):
    """Update selected data source for one or more task types."""
    try:
        ok = await asyncio.to_thread(save_selected_sources, payload.sources)
        return {"ok": ok}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
