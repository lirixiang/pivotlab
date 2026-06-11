#!/usr/bin/env python3
"""Standalone training worker — runs as subprocess, one model per GPU.

Usage:
    CUDA_VISIBLE_DEVICES=0 python train_worker.py /path/to/task.json

The task JSON contains:
  { "task_id", "model_type", "max_stocks", "min_days", "epochs",
    "label_method", "pct_threshold", "gpu_id", "progress_file" }

Progress is written to progress_file as JSON, polled by the API server.
"""
import json
import os
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
logger = logging.getLogger("train_worker")


def write_progress(path: str, **kw):
    """Atomically write progress JSON."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(kw, f)
    os.replace(tmp, path)


# Will be set per-task
_base_kw: dict = {}


def wprog(path: str, **kw):
    """Write progress with base fields merged."""
    merged = {**_base_kw, **kw}
    write_progress(path, **merged)


def main():
    global _base_kw
    if len(sys.argv) < 2:
        print("Usage: python train_worker.py <task.json>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        task = json.load(f)

    task_id = task["task_id"]
    model_type = task["model_type"]
    max_stocks = task.get("max_stocks", 200)
    min_days = task.get("min_days", 200)
    epochs = task.get("epochs", 100)
    label_method = task.get("label_method", "zigzag")
    pct_threshold = task.get("pct_threshold", 5.0)
    pf = task["progress_file"]
    gpu_id = task.get("gpu_id", 0)
    t0 = time.time()

    _base_kw = dict(task_id=task_id, model_type=model_type, gpu_id=gpu_id,
                    max_stocks=max_stocks, epochs=epochs,
                    started_at=t0, ended_at=None, result=None,
                    codes_used=0, total_codes=0)

    logger.info("Worker pid=%d task=%s model=%s gpu=%s stocks=%d",
                os.getpid(), task_id, model_type,
                os.environ.get("CUDA_VISIBLE_DEVICES", "all"), max_stocks)

    wprog(pf, status="loading", progress=0, message="从数据库加载股票数据...")

    # ── Load data ──
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import Session
        sync_url = os.environ.get("DATABASE_URL", "").strip().replace("+asyncpg", "+psycopg2")
        engine = create_engine(sync_url)

        with Session(engine) as session:
            rows = session.execute(text(
                "SELECT code FROM daily_candles "
                "GROUP BY code HAVING COUNT(*) >= :min_days "
                "ORDER BY random() LIMIT :limit"
            ), {"min_days": min_days, "limit": max_stocks}).fetchall()
            codes = [r[0] for r in rows]

        if not codes:
            wprog(pf, status="failed", progress=0, message="数据库中没有足够的K线数据", ended_at=time.time())
            sys.exit(1)

        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from app.services.data_provider import _cache_read

        candle_lists = [c for code in codes if (c := _cache_read(code, limit=730)) and len(c) >= min_days]
        total_codes = len(codes)
        _base_kw.update(codes_used=len(candle_lists), total_codes=total_codes)

        if not candle_lists:
            wprog(pf, status="failed", progress=0, message="加载K线数据失败", ended_at=time.time())
            sys.exit(1)

        logger.info("Loaded %d/%d stocks from DB in %.1fs", len(candle_lists), total_codes, time.time() - t0)
        wprog(pf, status="training", progress=5,
              message=f"已加载 {len(candle_lists)} 只股票，开始 {model_type} 训练...")

    except Exception as e:
        logger.exception("Data loading failed")
        wprog(pf, status="failed", progress=0, message=f"数据加载失败: {e}", ended_at=time.time())
        sys.exit(1)

    # ── Train ──
    try:
        def progress_cb(pct, msg):
            wprog(pf, status="training", progress=min(pct, 95), message=msg)

        from app.services.ai_strategy import train_model
        result = train_model(candle_lists, model_type=model_type, label_method=label_method,
                             pct_threshold=pct_threshold, epochs=epochs, progress_cb=progress_cb)
        result["codes_used"] = len(candle_lists)
        result["total_market_codes"] = total_codes

        # Serialize numpy
        def _clean(obj):
            if isinstance(obj, dict):
                return {k: _clean(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [_clean(v) for v in obj]
            if hasattr(obj, "item"):
                return obj.item()
            return obj

        wprog(pf, status="completed", progress=100, message="训练完成",
              ended_at=time.time(), result=_clean(result))
        logger.info("Done: %s  elapsed=%.1fs", model_type, time.time() - t0)

    except Exception as e:
        logger.exception("Training failed")
        wprog(pf, status="failed", progress=0, message=f"训练失败: {e}", ended_at=time.time())
        sys.exit(1)


if __name__ == "__main__":
    main()
