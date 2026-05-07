import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import init_db
from .routers import market, screener, stocks, watchlist, sync, settings, backtest, algo, strategy
from .services.data_provider import preload_candles
from .services.sync_worker import spawn_sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

# Default schedule config — all disabled
DEFAULT_SCHEDULE: dict[str, dict] = {
    "daily_candles":        {"enabled": False, "cron": "0 16 * * 1-5",  "label": "历史日线",     "desc": "每交易日收盘后同步"},
    "financials":           {"enabled": False, "cron": "0 18 * * 6",    "label": "基本面快照",   "desc": "每周六更新（季报期可手动）"},
    "analyst_consensus":    {"enabled": False, "cron": "0 18 * * 3",    "label": "机构一致预期", "desc": "每周三更新"},
    "quotes":               {"enabled": False, "cron": "5 15 * * 1-5",  "label": "实时行情",     "desc": "每交易日收盘后同步"},
    "stocks":               {"enabled": False, "cron": "0 8 * * 1",     "label": "股票列表",     "desc": "每周一更新"},
    "concepts":             {"enabled": False, "cron": "10 8 * * 1",    "label": "题材与概念",   "desc": "每周一更新"},
    "industry":             {"enabled": False, "cron": "20 8 * * 1",    "label": "行业数据",     "desc": "每周一更新"},
}


def _load_schedule_config() -> dict[str, dict]:
    """Load schedule config from DB, merging with defaults."""
    from sqlalchemy import create_engine, text
    from .database import DATABASE_URL
    url = str(DATABASE_URL).replace("sqlite+aiosqlite", "sqlite").replace("postgresql+asyncpg", "postgresql+psycopg2")
    try:
        eng = create_engine(url)
        with eng.connect() as conn:
            row = conn.execute(text("SELECT value FROM user_settings WHERE key = 'schedule_config'")).fetchone()
        eng.dispose()
        if row:
            import json
            saved = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            # Merge: saved overrides defaults
            merged = {}
            for k, v in DEFAULT_SCHEDULE.items():
                merged[k] = {**v, **saved.get(k, {})}
            return merged
    except Exception as e:
        logger.warning("Failed to load schedule config: %s", e)
    return {k: {**v} for k, v in DEFAULT_SCHEDULE.items()}


def _apply_schedule(scheduler: BackgroundScheduler, config: dict[str, dict]):
    """Apply schedule config to the scheduler, adding/removing jobs as needed."""
    for task_type, cfg in config.items():
        job_id = f"sched_{task_type}"
        # Remove existing job if any
        existing = scheduler.get_job(job_id)
        if existing:
            scheduler.remove_job(job_id)

        if cfg.get("enabled"):
            try:
                trigger = CronTrigger.from_crontab(cfg["cron"])
                scheduler.add_job(
                    spawn_sync, trigger,
                    args=[task_type],
                    id=job_id,
                    name=cfg.get("label", task_type),
                    replace_existing=True,
                )
                logger.info("Scheduled %s: %s", task_type, cfg["cron"])
            except Exception as e:
                logger.error("Bad cron for %s: %s", task_type, e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    await init_db()
    # Only start scheduler in one worker (the first one)
    if os.environ.get("SCHEDULER_DISABLED") != "1":
        _scheduler = BackgroundScheduler()
        _scheduler.add_job(
            preload_candles, "interval", minutes=30,
            next_run_time=datetime.now(), id="preload_candles",
        )
        # Load and apply user-configured schedules
        config = _load_schedule_config()
        _apply_schedule(_scheduler, config)
        _scheduler.start()
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)


app = FastAPI(title="PivotLab API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market.router)
app.include_router(stocks.router)
app.include_router(screener.router)
app.include_router(watchlist.router)
app.include_router(sync.router)
app.include_router(settings.router)
app.include_router(backtest.router)
app.include_router(algo.router)
app.include_router(strategy.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "pivotlab"}
