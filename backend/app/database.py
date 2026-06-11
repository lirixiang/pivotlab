"""Database adapter layer — Strategy pattern for multi-database support.

Set DATABASE_URL env var to switch backend:
  PostgreSQL: postgresql+asyncpg://user:pass@host:port/dbname
  SQLite:     sqlite+aiosqlite:///data/pivotlab.db  (default)
"""

import os
import re

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/pivotlab.db")

# Normalize any Postgres URL to the asyncpg driver so HF Spaces env vars work
# regardless of prefix: postgres://, postgresql://, postgresql+psycopg2://, etc.
if re.match(r"^postgres(ql)?(\+\w+)?://", DATABASE_URL):
    DATABASE_URL = re.sub(r"^postgres(ql)?(\+\w+)?://", "postgresql+asyncpg://", DATABASE_URL, count=1)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── Strategy ───────────────────────────────────────────────────────────────

class _DBStrategy:
    supports_concurrent_writes: bool = True

    def create_engine(self, url: str):
        raise NotImplementedError


class _PostgresStrategy(_DBStrategy):
    supports_concurrent_writes = True

    def create_engine(self, url: str):
        # Supabase / pgbouncer transaction pooler (port 6543) does NOT support
        # prepared statements, which asyncpg uses by default. Disable the
        # statement cache so pooled connections don't blow up mid-query.
        return create_async_engine(
            url, echo=False,
            pool_size=2, max_overflow=1,
            pool_pre_ping=True, pool_recycle=300,
            connect_args={
                "statement_cache_size": 0,
                "prepared_statement_cache_size": 0,
            },
        )


class _SQLiteStrategy(_DBStrategy):
    supports_concurrent_writes = False

    def create_engine(self, url: str):
        db_path = url.split("///", 1)[1]
        if not os.path.isabs(db_path):
            db_path = os.path.join(_BASE_DIR, db_path)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        url = f"sqlite+aiosqlite:///{db_path}"

        eng = create_async_engine(
            url, echo=False,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(eng.sync_engine, "connect")
        def _set_pragma(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

        return eng


def _pick_strategy(url: str) -> _DBStrategy:
    if url.startswith("sqlite"):
        return _SQLiteStrategy()
    return _PostgresStrategy()


# ── Public objects ─────────────────────────────────────────────────────────

db_strategy = _pick_strategy(DATABASE_URL)
engine = db_strategy.create_engine(DATABASE_URL)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    from . import models  # noqa: F401
    from .quant import models as _quant_models  # noqa: F401  M1 量化系统表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
