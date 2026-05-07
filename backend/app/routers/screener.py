"""Screener endpoints — scan runs in subprocess, results cached to JSON files."""
import json
import os
from datetime import datetime

from fastapi import APIRouter, Query

from ..schemas import ScreenerResponse, ScreenerItem
from ..services.sync_worker import spawn_sync

router = APIRouter(prefix="/api/screener", tags=["screener"])

_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".screener_cache")


def _read_cache(pattern: str, limit: int, min_score: float) -> ScreenerResponse:
    path = os.path.join(_CACHE_DIR, f"{pattern}.json")
    if not os.path.exists(path):
        return ScreenerResponse(pattern=pattern, total=0, scanned=0, scanned_at=datetime.now(), items=[])
    try:
        with open(path, "r") as f:
            data = json.load(f)
        items = [ScreenerItem(**it) for it in data.get("items", []) if it.get("score", 0) >= min_score]
        return ScreenerResponse(
            pattern=pattern,
            total=data.get("total", len(items)),
            scanned=data.get("scanned", 0),
            scanned_at=datetime.fromisoformat(data["scanned_at"]) if data.get("scanned_at") else datetime.now(),
            items=items[:limit],
        )
    except Exception:
        return ScreenerResponse(pattern=pattern, total=0, scanned=0, scanned_at=datetime.now(), items=[])


@router.post("/scan")
async def trigger_scan():
    """Trigger screener scan in a separate process."""
    started = spawn_sync("screener")
    if started:
        return {"status": "started", "message": "筛选扫描已启动，请稍后刷新查看结果"}
    return {"status": "already_running", "message": "筛选正在进行中，请稍后查看结果"}


@router.get("/{pattern}", response_model=ScreenerResponse)
async def get_results(
    pattern: str,
    limit: int = Query(50, ge=1, le=200),
    min_score: float = Query(0, ge=0, le=100),
):
    """Return cached screener results (from last scan)."""
    return _read_cache(pattern, limit, min_score)


@router.get("/")
async def summary():
    """Counts per pattern from cache."""
    bp = _read_cache("breakout_pullback", 9999, 0)
    bs = _read_cache("bottom_stabilize", 9999, 0)
    return {
        "scanned": max(bp.scanned, bs.scanned),
        "counts": {
            "breakout_pullback": bp.total,
            "bottom_stabilize": bs.total,
        },
        "scanned_at": max(bp.scanned_at, bs.scanned_at),
    }
