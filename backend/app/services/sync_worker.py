"""Sync worker — runs sync tasks in a separate process.

All sync tasks are spawned as child processes via subprocess.Popen
so they never block the main FastAPI event loop or its thread pools.
Each process runs a small Python script that imports sync_service,
does its work, writes results to the DB, and exits.

Usage from the router:
    from ..services.sync_worker import spawn_sync
    spawn_sync("stocks")              # fire-and-forget
    spawn_sync("daily_candles", days=365)
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)

# Path to the worker entry script
_WORKER_SCRIPT = os.path.join(os.path.dirname(__file__), "_sync_run.py")


def spawn_sync(task_type: str, **kwargs) -> bool:
    """Spawn a sync task in a separate OS process (fire-and-forget).

    Returns False if same task_type is already running.
    Creates the task record in the main process first to prevent races,
    then passes SYNC_TASK_ID to the child process.
    """
    from .sync_service import _create_task
    task_id = _create_task(task_type)
    if task_id == -1:
        logger.info("Skipping %s: already running", task_type)
        return False

    env = os.environ.copy()
    env["SYNC_TASK_TYPE"] = task_type
    env["SYNC_TASK_ID"] = str(task_id)
    env["PYTHONUNBUFFERED"] = "1"
    if kwargs:
        env["SYNC_TASK_KWARGS"] = json.dumps(kwargs)

    # Run from the backend directory so relative imports work
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    log_file = os.path.join(backend_dir, f".sync_{task_type}.log")
    log_fh = open(log_file, "w")
    p = subprocess.Popen(
        [sys.executable, _WORKER_SCRIPT],
        env=env,
        cwd=backend_dir,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # fully detached from parent
    )
    logger.info("Spawned sync process %s (pid=%s, task_id=%s)", task_type, p.pid, task_id)
    return True

