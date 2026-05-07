#!/usr/bin/env python3
"""AI Stock Scanner — runs as subprocess, scans stocks for buy/sell signals.

Usage:
    CUDA_VISIBLE_DEVICES=0 python scan_worker.py /path/to/scan_task.json

Task JSON:
  { "task_id", "scope", "scope_code", "model_types",
    "buy_threshold", "sell_threshold", "progress_file" }

Progress is written to progress_file as JSON, polled by the API server.
"""
import json
import os
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
logger = logging.getLogger("scan_worker")


def write_progress(path: str, **kw):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(kw, f, ensure_ascii=False)
    os.replace(tmp, path)


_base_kw: dict = {}


def wprog(path: str, **kw):
    merged = {**_base_kw, **kw}
    write_progress(path, **merged)


def main():
    global _base_kw
    if len(sys.argv) < 2:
        print("Usage: python scan_worker.py <task.json>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        task = json.load(f)

    task_id = task["task_id"]
    scope = task.get("scope", "watchlist")  # watchlist / industry / cached
    scope_code = task.get("scope_code", "")
    model_types = task.get("model_types", ["lightgbm"])
    buy_threshold = task.get("buy_threshold", 0.35)
    sell_threshold = task.get("sell_threshold", 0.35)
    pf = task["progress_file"]
    t0 = time.time()

    _base_kw = dict(task_id=task_id, scope=scope, started_at=t0,
                    ended_at=None, results=[], total=0, scanned=0)

    logger.info("Scanner pid=%d task=%s scope=%s models=%s",
                os.getpid(), task_id, scope, model_types)

    wprog(pf, status="loading", progress=0, message="加载股票列表...")

    # ── Determine stock codes to scan ──
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import Session
        sync_url = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "+psycopg2")
        engine = create_engine(sync_url)

        codes_names: list[tuple[str, str]] = []

        if scope == "watchlist":
            with Session(engine) as s:
                rows = s.execute(text("SELECT code, name FROM watchlist ORDER BY code")).fetchall()
                codes_names = [(r[0], r[1]) for r in rows]

        elif scope == "industry":
            with Session(engine) as s:
                # Get industry of scope_code
                ind = s.execute(text("SELECT industry FROM stocks WHERE code = :c"),
                                {"c": scope_code}).scalar()
                if ind:
                    rows = s.execute(text(
                        "SELECT s.code, s.name FROM stocks s "
                        "INNER JOIN (SELECT code FROM daily_candles GROUP BY code HAVING COUNT(*) >= 100) dc "
                        "ON s.code = dc.code "
                        "WHERE s.industry = :ind AND s.is_st = false ORDER BY s.code"
                    ), {"ind": ind}).fetchall()
                    codes_names = [(r[0], r[1]) for r in rows]

        elif scope == "cached":
            with Session(engine) as s:
                rows = s.execute(text(
                    "SELECT s.code, s.name FROM stocks s "
                    "INNER JOIN (SELECT code FROM daily_candles GROUP BY code HAVING COUNT(*) >= 100) dc "
                    "ON s.code = dc.code "
                    "WHERE s.is_st = false ORDER BY s.code"
                )).fetchall()
                codes_names = [(r[0], r[1]) for r in rows]

        if not codes_names:
            wprog(pf, status="failed", progress=0, message="没有找到可扫描的股票",
                  ended_at=time.time())
            sys.exit(1)

        _base_kw["total"] = len(codes_names)
        logger.info("Scanning %d stocks in scope=%s", len(codes_names), scope)
        wprog(pf, status="scanning", progress=5,
              message=f"开始扫描 {len(codes_names)} 只股票...")

    except Exception as e:
        logger.exception("Failed to load stock list")
        wprog(pf, status="failed", progress=0, message=f"加载失败: {e}",
              ended_at=time.time())
        sys.exit(1)

    # ── Load candle data and run predictions ──
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from app.services.data_provider import _cache_read
        from app.services.ai_strategy import generate_ai_signal, model_status

        # Check which models are actually trained
        status = model_status()
        available_models = [mt for mt in model_types if status.get(mt, {}).get("trained")]

        if not available_models:
            wprog(pf, status="failed", progress=0,
                  message=f"没有已训练的模型 (需要: {', '.join(model_types)})",
                  ended_at=time.time())
            sys.exit(1)

        logger.info("Available models: %s", available_models)

        hits: list[dict] = []
        scanned = 0

        for code, name in codes_names:
            scanned += 1
            pct = 5 + int(90 * scanned / len(codes_names))

            if scanned % 10 == 0 or scanned == len(codes_names):
                wprog(pf, status="scanning", progress=pct, scanned=scanned,
                      message=f"扫描中 {scanned}/{len(codes_names)} ... 已发现 {len(hits)} 个信号",
                      results=hits)

            candles = _cache_read(code, limit=500)
            if not candles or len(candles) < 100:
                continue

            for mt in available_models:
                try:
                    sig = generate_ai_signal(
                        candles,
                        model_type=mt,
                        buy_threshold=buy_threshold,
                        sell_threshold=sell_threshold,
                    )
                    if sig.get("action") in ("buy", "sell") and sig.get("confidence", 0) > 0:
                        hits.append({
                            "code": code,
                            "name": name,
                            "model": mt,
                            "action": sig["action"],
                            "confidence": sig["confidence"],
                            "current_price": sig["current_price"],
                            "entry_price": sig["entry_price"],
                            "stop_loss": sig["stop_loss"],
                            "target_price": sig["target_price"],
                            "risk_reward": sig["risk_reward"],
                            "trend": sig.get("trend", ""),
                            "reason": sig.get("reason", ""),
                        })
                except Exception as e:
                    logger.debug("Predict failed %s/%s: %s", code, mt, e)

        # Final result
        # Sort by confidence descending
        hits.sort(key=lambda x: x["confidence"], reverse=True)

        wprog(pf, status="completed", progress=100, scanned=scanned,
              message=f"扫描完成: {len(codes_names)} 只股票, 发现 {len(hits)} 个信号",
              ended_at=time.time(), results=hits)
        logger.info("Done: scanned=%d hits=%d elapsed=%.1fs",
                    scanned, len(hits), time.time() - t0)

    except Exception as e:
        logger.exception("Scan failed")
        wprog(pf, status="failed", progress=0, message=f"扫描失败: {e}",
              ended_at=time.time())
        sys.exit(1)


if __name__ == "__main__":
    main()
