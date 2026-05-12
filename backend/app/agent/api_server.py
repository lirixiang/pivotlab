from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.config import get_settings
from app.agent.core import session as session_db
from app.agent.knowledge.store import init_kb
from app.agent.observability.logger import configure_logging, get_logger
from app.routers.agent import router as agent_router

log = get_logger("agent.standalone")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    await session_db.init_db()
    try:
        await init_kb()
    except Exception as exc:  # noqa: BLE001
        log.warning("kb_init_skipped", error=str(exc))
    yield


app = FastAPI(title="PivotLab Agent API", version="0.1.0", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "pivotlab-agent"}