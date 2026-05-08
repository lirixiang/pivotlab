"""API routes for AI Strategy — train, predict, signal, backtest."""
import asyncio
import json
import logging
import os
import subprocess
import time as _time
import uuid
from pathlib import Path

import ray
from fastapi import APIRouter
from sqlalchemy import select, func

from ..database import AsyncSessionLocal
from ..models import Stock
from ..services.data_provider import get_candles

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/strategy", tags=["strategy"])

# ─── Ray Distributed Training Manager ───
_NUM_GPUS = int(os.environ.get("NUM_GPUS", "6"))


@router.get("/status")
async def status():
    """Check trained model status."""
    from ..services.ai_strategy import model_status
    return model_status()


@router.post("/labels/{code}")
async def labels(code: str, body: dict | None = None):
    """Get labeled buy/sell points for a stock (for visualization)."""
    body = body or {}
    method = body.get("method", "zigzag")
    pct = float(body.get("pct_threshold", 5.0))

    from ..services.labeler import get_labeled_points

    candles = get_candles(code, period="daily", days=500)
    if not candles:
        return {"error": "no candle data"}

    def _run():
        return get_labeled_points(candles, method=method, pct_threshold=pct)

    loop = asyncio.get_running_loop()
    points = await loop.run_in_executor(None, _run)
    return {
        "code": code,
        "method": method,
        "points": points,
        "candles": [
            {"date": c.date, "open": c.open, "high": c.high,
             "low": c.low, "close": c.close, "volume": c.volume}
            for c in candles
        ],
    }


@router.post("/train")
async def train(body: dict):
    """Train AI strategy model.

    Body: {
      "codes": ["600519", "000001", ...],
      "model_type": "lightgbm" | "transformer" | "lstm" | "cnn_lstm",
      "label_method": "zigzag" | "dp",
      "pct_threshold": 5.0,
      "epochs": 50,  // transformer only
    }
    """
    codes = body.get("codes", [])
    model_type = body.get("model_type", "lightgbm")
    label_method = body.get("label_method", "zigzag")
    pct_threshold = float(body.get("pct_threshold", 5.0))
    epochs = min(int(body.get("epochs", 50)), 200)

    if not codes:
        return {"error": "provide a list of stock codes"}

    from ..services.ai_strategy import train_model

    candle_lists = []
    for code in codes[:100]:
        c = get_candles(code, period="daily", days=500)
        if c and len(c) >= 100:
            candle_lists.append(c)

    if not candle_lists:
        return {"error": "no sufficient candle data"}

    def _run():
        return train_model(
            candle_lists,
            model_type=model_type,
            label_method=label_method,
            pct_threshold=pct_threshold,
            epochs=epochs,
        )

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run)
    result["codes_used"] = len(candle_lists)
    return result


@router.get("/predict/{code}")
async def predict_signal(code: str, model_type: str = "lightgbm"):
    """Get AI prediction for latest bar."""
    from ..services.ai_strategy import predict

    candles = get_candles(code, period="daily", days=500)
    if not candles:
        return {"error": "no candle data"}

    result = predict(candles, model_type=model_type)  # type: ignore
    if result is None:
        return {"error": f"{model_type} model not trained yet"}
    result["code"] = code
    return result


@router.post("/signal/{code}")
async def signal(code: str, body: dict | None = None):
    """Generate full trading signal (entry/stop/target) from AI model."""
    body = body or {}
    model_type = body.get("model_type", "lightgbm")
    buy_threshold = float(body.get("buy_threshold", 0.4))
    sell_threshold = float(body.get("sell_threshold", 0.4))

    from ..services.ai_strategy import generate_ai_signal

    candles = get_candles(code, period="daily", days=500)
    if not candles:
        return {"error": "no candle data"}

    def _run():
        return generate_ai_signal(
            candles,
            model_type=model_type,  # type: ignore
            buy_threshold=buy_threshold,
            sell_threshold=sell_threshold,
        )

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run)
    result["code"] = code
    result["candles"] = [
        {"date": c.date, "open": c.open, "high": c.high,
         "low": c.low, "close": c.close, "volume": c.volume}
        for c in candles[-180:]
    ]
    return result


@router.post("/backtest")
async def backtest(body: dict):
    """Run backtest using AI strategy.

    Body: {
      "code": "600519",
      "model_type": "lightgbm" | "transformer",
      "buy_threshold": 0.4,
      "sell_threshold": 0.4,
      "stop_atr_mult": 2.0,
      "target_atr_mult": 3.0,
      "max_hold_bars": 20,
    }
    """
    code = body.get("code", "000001")
    model_type = body.get("model_type", "lightgbm")
    buy_threshold = float(body.get("buy_threshold", 0.4))
    sell_threshold = float(body.get("sell_threshold", 0.4))
    stop_atr_mult = float(body.get("stop_atr_mult", 2.0))
    target_atr_mult = float(body.get("target_atr_mult", 3.0))
    max_hold_bars = int(body.get("max_hold_bars", 20))

    from ..services.ai_strategy import ai_backtest

    candles = get_candles(code, period="daily", days=500)
    if not candles:
        return {"error": "no candle data"}

    def _run():
        return ai_backtest(
            candles,
            model_type=model_type,  # type: ignore
            buy_threshold=buy_threshold,
            sell_threshold=sell_threshold,
            stop_atr_mult=stop_atr_mult,
            target_atr_mult=target_atr_mult,
            max_hold_bars=max_hold_bars,
        )

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run)
    result["code"] = code
    result["candles"] = [
        {"date": c.date, "open": c.open, "high": c.high,
         "low": c.low, "close": c.close, "volume": c.volume}
        for c in candles
    ]
    return result


@router.get("/industry_stocks/{code}")
async def industry_stocks(code: str, limit: int = 20):
    """Get stocks in the same industry as *code*."""
    async with AsyncSessionLocal() as s:
        r = await s.execute(select(Stock.industry).where(Stock.code == code))
        industry = r.scalar()
        if not industry:
            return {"code": code, "industry": "", "stocks": []}
        r2 = await s.execute(
            select(Stock.code, Stock.name)
            .where(Stock.industry == industry, Stock.is_st == False)
            .order_by(Stock.code)
            .limit(limit)
        )
        stocks = [{"code": row[0], "name": row[1]} for row in r2.all()]
    return {"code": code, "industry": industry, "stocks": stocks}


def _fetch_market_candles(max_stocks: int, min_days: int) -> tuple[list, int]:
    """Read candle data directly from DB cache. No network calls."""
    from ..services.data_provider import _cache_read, _sync_engine
    from sqlalchemy import text
    from sqlalchemy.orm import Session as SaSession

    with SaSession(_sync_engine) as session:
        rows = session.execute(text(
            "SELECT code FROM daily_candles "
            "GROUP BY code HAVING COUNT(*) >= :min_days "
            "ORDER BY random() LIMIT :limit"
        ), {"min_days": min_days, "limit": max_stocks}).fetchall()
        codes = [r[0] for r in rows]

    total = len(codes)
    logger.info("DB has %d stocks with >= %d days, reading...", total, min_days)

    candle_lists = []
    for code in codes:
        c = _cache_read(code, limit=730)
        if c and len(c) >= min_days:
            candle_lists.append(c)

    logger.info("Loaded %d stocks from DB cache", len(candle_lists))
    return candle_lists, total


@router.post("/train_market")
async def train_market(body: dict):
    """Launch distributed training via Ray.

    Body: { model_type, max_stocks, epochs, min_days, label_method, pct_threshold, num_gpus }

    Features:
      - model_type="all" → 5 models in parallel across GPUs
      - num_gpus > 1 → DDP multi-GPU training for PyTorch models
      - Ray manages GPU allocation, no manual CUDA_VISIBLE_DEVICES
    """
    from ..services.ray_trainer import submit_training

    model_type = body.get("model_type", "rl_ppo")
    max_stocks = min(int(body.get("max_stocks", 200)), 5000)
    epochs = min(int(body.get("epochs", 100)), 500)
    min_days = int(body.get("min_days", 200))
    label_method = body.get("label_method", "zigzag")
    pct_threshold = float(body.get("pct_threshold", 5.0))
    num_gpus = min(int(body.get("num_gpus", 1)), _NUM_GPUS)

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: submit_training(
        model_type=model_type,
        max_stocks=max_stocks,
        epochs=epochs,
        min_days=min_days,
        label_method=label_method,
        pct_threshold=pct_threshold,
        num_gpus=num_gpus,
    ))
    return result


@router.get("/train_progress")
async def train_progress():
    """Get all training task statuses from Ray ProgressActor."""
    from ..services.ray_trainer import get_progress_actor
    progress = get_progress_actor()
    return await asyncio.get_running_loop().run_in_executor(
        None, lambda: ray.get(progress.get_all.remote())
    )


@router.get("/train_progress/{task_id}")
async def train_progress_by_id(task_id: str):
    """Get progress of a specific training task."""
    from ..services.ray_trainer import get_progress_actor
    progress = get_progress_actor()
    task = await asyncio.get_running_loop().run_in_executor(
        None, lambda: ray.get(progress.get.remote(task_id))
    )
    if not task:
        return {"error": "task not found"}
    return task


@router.delete("/train_progress/{task_id}")
async def cancel_training(task_id: str):
    """Cancel a running training task."""
    from ..services.ray_trainer import get_progress_actor
    progress = get_progress_actor()
    task = ray.get(progress.get.remote(task_id))
    if task:
        ray.get(progress.update.remote(
            task_id, status="cancelled", message="已取消", ended_at=_time.time(),
        ))
    return {"status": "cancelled", "task_id": task_id}


@router.delete("/train_progress")
async def clear_history():
    """Clear completed/failed task history."""
    from ..services.ray_trainer import get_progress_actor
    progress = get_progress_actor()
    removed = await asyncio.get_running_loop().run_in_executor(
        None, lambda: ray.get(progress.clear_finished.remote())
    )
    return {"removed": removed}


# ─── AI Stock Scanner (subprocess) ───
_SCAN_DIR = Path("/app/backend/models/.scan_progress")
_SCAN_DIR.mkdir(parents=True, exist_ok=True)
_scan_workers: dict[str, subprocess.Popen] = {}


def _scan_file(task_id: str) -> Path:
    return _SCAN_DIR / f"{task_id}.json"


def _read_scan(task_id: str) -> dict | None:
    p = _scan_file(task_id)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _all_scans() -> list[dict]:
    tasks = []
    for p in sorted(_SCAN_DIR.glob("*.json"), reverse=True):
        if p.name.endswith(".tmp") or "_task.json" in p.name:
            continue
        try:
            tasks.append(json.loads(p.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    # Clean up dead workers
    for tid in list(_scan_workers):
        proc = _scan_workers[tid]
        if proc.poll() is not None:
            del _scan_workers[tid]
    return tasks


@router.post("/scan")
async def start_scan(body: dict):
    """Launch AI stock scanner as subprocess.

    Body: {
      "scope": "watchlist" | "industry" | "cached",
      "scope_code": "600519",   // required for industry scope
      "model_types": ["lightgbm", "transformer"],
      "buy_threshold": 0.35,
      "sell_threshold": 0.35,
    }
    """
    scope = body.get("scope", "watchlist")
    scope_code = body.get("scope_code", "")
    model_types = body.get("model_types", ["lightgbm"])
    buy_threshold = float(body.get("buy_threshold", 0.35))
    sell_threshold = float(body.get("sell_threshold", 0.35))

    # Check if a scan is already running
    for scan in _all_scans():
        if scan.get("status") in ("loading", "scanning"):
            return {"error": "已有扫描任务在运行中", "task": scan}

    task_id = uuid.uuid4().hex[:8]
    progress_path = str(_scan_file(task_id))

    # Write initial progress
    init = {
        "task_id": task_id, "scope": scope, "scope_code": scope_code,
        "model_types": model_types,
        "status": "pending", "progress": 0, "message": "启动中...",
        "started_at": _time.time(), "ended_at": None,
        "results": [], "total": 0, "scanned": 0,
    }
    Path(progress_path).write_text(json.dumps(init, ensure_ascii=False))

    # Write task config
    task_config = {
        "task_id": task_id, "scope": scope, "scope_code": scope_code,
        "model_types": model_types,
        "buy_threshold": buy_threshold, "sell_threshold": sell_threshold,
        "progress_file": progress_path,
    }
    task_file = str(_SCAN_DIR / f"{task_id}_task.json")
    Path(task_file).write_text(json.dumps(task_config, ensure_ascii=False))

    # Spawn worker — CPU only, doesn't need GPU
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = "0"

    log_file = open(str(_SCAN_DIR / f"{task_id}.log"), "w")
    proc = subprocess.Popen(
        ["python", "/app/backend/scan_worker.py", task_file],
        cwd="/app/backend",
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    _scan_workers[task_id] = proc

    logger.info("Launched scanner: task=%s scope=%s pid=%d", task_id, scope, proc.pid)
    return {"task_id": task_id, "scope": scope, "status": "pending",
            "message": "扫描任务已启动"}


@router.get("/scan_progress")
async def scan_progress():
    """Get all scan task statuses."""
    return _all_scans()


@router.get("/scan_progress/{task_id}")
async def scan_progress_by_id(task_id: str):
    task = _read_scan(task_id)
    if not task:
        return {"error": "task not found"}
    return task


@router.delete("/scan_progress/{task_id}")
async def cancel_scan(task_id: str):
    proc = _scan_workers.get(task_id)
    if proc and proc.poll() is None:
        proc.terminate()
        del _scan_workers[task_id]
    p = _scan_file(task_id)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            data["status"] = "cancelled"
            data["message"] = "已取消"
            data["ended_at"] = _time.time()
            p.write_text(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass
    return {"status": "cancelled", "task_id": task_id}


@router.delete("/scan_progress")
async def clear_scan_history():
    removed = 0
    for p in _SCAN_DIR.glob("*.json"):
        if p.name.endswith(".tmp"):
            p.unlink(missing_ok=True)
            continue
        try:
            data = json.loads(p.read_text())
            if data.get("status") in ("completed", "failed", "cancelled"):
                p.unlink()
                task_cfg = _SCAN_DIR / f"{data.get('task_id', '')}_task.json"
                task_cfg.unlink(missing_ok=True)
                removed += 1
        except Exception:
            pass
    return {"removed": removed}
