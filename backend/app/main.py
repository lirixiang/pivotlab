import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import init_db
from .routers import market, screener, stocks, watchlist, sync, settings, backtest
from .services.data_provider import preload_candles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s")

_scheduler: BackgroundScheduler | None = None


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


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "pivotlab"}
