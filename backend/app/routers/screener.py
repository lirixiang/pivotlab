"""Screener endpoints — scan runs in subprocess, results cached to JSON files."""
import glob
import json
import os
import re
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..schemas import ScreenerResponse, ScreenerItem
from ..services.sync_worker import spawn_sync
from ..services.screener import get_config, update_config, PATTERN_DETECTORS, MODEL_LABELS

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


class ConfigUpdate(BaseModel):
    pattern: str
    params: dict


@router.get("/config")
async def get_screener_config():
    """Return all model configs."""
    return get_config()


@router.post("/config")
async def update_screener_config(body: ConfigUpdate):
    """Update config for a specific pattern."""
    ok = update_config(body.pattern, **body.params)
    if not ok:
        return {"status": "error", "message": f"Unknown pattern: {body.pattern}"}
    return {"status": "ok", "config": get_config()[body.pattern]}


@router.get("/history/{pattern}")
async def get_history(pattern: str, limit: int = Query(30, ge=1, le=100)):
    """List available history snapshots for a pattern, newest first."""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    files = sorted(glob.glob(os.path.join(_CACHE_DIR, f"{pattern}_*.json")), reverse=True)
    entries = []
    for fp in files[:limit]:
        fname = os.path.basename(fp)
        # Extract timestamp from filename: pattern_YYYYMMDD_HHMM.json
        m = re.search(r"_(\d{8}_\d{4})\.json$", fname)
        if not m:
            continue
        ts = m.group(1)
        try:
            with open(fp, "r") as f:
                data = json.load(f)
            entries.append({
                "ts": ts,
                "scanned_at": data.get("scanned_at", ""),
                "total": data.get("total", 0),
                "scanned": data.get("scanned", 0),
            })
        except Exception:
            continue
    return entries


@router.get("/history/{pattern}/{ts}")
async def get_history_snapshot(
    pattern: str,
    ts: str,
    limit: int = Query(200, ge=1, le=500),
    min_score: float = Query(0, ge=0, le=100),
):
    """Load a specific history snapshot by timestamp."""
    # Validate ts format to prevent path traversal
    if not re.match(r"^\d{8}_\d{4}$", ts):
        return ScreenerResponse(pattern=pattern, total=0, scanned=0, scanned_at=datetime.now(), items=[])
    return _read_cache(f"{pattern}_{ts}", limit, min_score)


@router.get("/summary")
async def summary():
    """Counts per pattern from cache."""
    results = {}
    scanned = 0
    scanned_at = datetime.min
    for p in PATTERN_DETECTORS:
        r = _read_cache(p, 9999, 0)
        results[p] = r.total
        scanned = max(scanned, r.scanned)
        scanned_at = max(scanned_at, r.scanned_at)
    return {
        "scanned": scanned,
        "counts": results,
        "labels": MODEL_LABELS,
        "scanned_at": scanned_at,
    }


@router.get("/{pattern}", response_model=ScreenerResponse)
async def get_results(
    pattern: str,
    limit: int = Query(50, ge=1, le=200),
    min_score: float = Query(0, ge=0, le=100),
):
    """Return cached screener results (from last scan)."""
    return _read_cache(pattern, limit, min_score)
