#!/usr/bin/env python3
"""Standalone sync worker entry point.

Invoked as a subprocess by sync_worker.spawn_sync().
Reads SYNC_TASK_TYPE and optional SYNC_TASK_KWARGS from environment,
runs the corresponding sync_service function, then exits.
"""
import json
import logging
import os
import signal
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
logger = logging.getLogger("sync_worker")

# Ensure the backend package is importable
_backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

TASK_REGISTRY = {
    "stocks":             "sync_stock_list",
    "quotes":             "sync_quotes",
    "financials":         "sync_financials",
    "financial_history":  "sync_financial_history",
    "concepts":           "sync_concepts",
    "industry":           "sync_industry",
    "daily_candles":      "sync_candles",
    "analyst_consensus":  "sync_analyst_consensus",
    "screener":           "run_screener",
}


def _signal_handler(signum, frame):
    logger.error("sync worker received signal %s (%s)", signum, signal.Signals(signum).name)
    sys.exit(128 + signum)


def main():
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGHUP, _signal_handler)
    task_type = os.environ.get("SYNC_TASK_TYPE", "")
    kwargs_json = os.environ.get("SYNC_TASK_KWARGS", "")
    task_id = os.environ.get("SYNC_TASK_ID", "")

    if not task_type:
        logger.error("SYNC_TASK_TYPE not set")
        sys.exit(1)

    fn_name = TASK_REGISTRY.get(task_type)
    if not fn_name:
        logger.error("Unknown task type: %s", task_type)
        sys.exit(1)

    kwargs = {}
    if kwargs_json:
        try:
            kwargs = json.loads(kwargs_json)
        except json.JSONDecodeError:
            logger.error("Invalid SYNC_TASK_KWARGS: %s", kwargs_json)
            sys.exit(1)

    # Pass pre-created task_id so sync functions skip _create_task
    if task_id:
        kwargs["_task_id"] = int(task_id)

    try:
        from app.services import sync_service
        fn = getattr(sync_service, fn_name)
        logger.info("Starting %s(%s)", fn_name, kwargs or "")
        fn(**kwargs) if kwargs else fn()
        logger.info("Finished %s", fn_name)
    except Exception:
        logger.exception("sync worker %s crashed", task_type)
        # Mark task as error if we have a task_id
        if task_id:
            try:
                from app.services.sync_service import _finish_task
                _finish_task(int(task_id), 0, 0, "subprocess crashed")
            except Exception:
                pass
        sys.exit(1)


if __name__ == "__main__":
    main()
