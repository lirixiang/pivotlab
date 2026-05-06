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


def spawn_sync(task_type: str, **kwargs) -> None:
    """Spawn a sync task in a separate OS process (fire-and-forget).

    The child process will:
      1. Import sync_service (with its own DB connections)
      2. Run the appropriate sync function
      3. Write progress/results to the sync_tasks table
      4. Exit cleanly
    """
    env = os.environ.copy()
    env["SYNC_TASK_TYPE"] = task_type
    if kwargs:
        env["SYNC_TASK_KWARGS"] = json.dumps(kwargs)

    # Run from the backend directory so relative imports work
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    p = subprocess.Popen(
        [sys.executable, _WORKER_SCRIPT],
        env=env,
        cwd=backend_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # fully detached from parent
    )
    logger.info("Spawned sync process %s (pid=%s)", task_type, p.pid)

