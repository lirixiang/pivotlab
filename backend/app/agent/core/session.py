"""Session storage in pivotlab PostgreSQL.

Tables (created on startup):
  agent_sessions(id, title, llm_provider, llm_model, created_at, updated_at)
  agent_messages(id, session_id, seq, role, content, tool_calls JSONB, tool_call_id, name, created_at)
  agent_traces(id, session_id, step, kind, payload JSONB, created_at)
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.config import get_settings
from app.agent.core.types import Message, ToolCall


_engine = None
_Session = None


async def init_db() -> None:
    global _engine, _Session
    s = get_settings()
    _engine = create_async_engine(s.db_url, pool_pre_ping=True)
    _Session = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS agent_sessions (
                id          TEXT PRIMARY KEY,
                title       TEXT,
                llm_provider TEXT,
                llm_model    TEXT,
                created_at  TIMESTAMPTZ DEFAULT NOW(),
                updated_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS agent_messages (
                id           TEXT PRIMARY KEY,
                session_id   TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
                seq          INTEGER NOT NULL,
                role         TEXT NOT NULL,
                content      TEXT,
                tool_calls   JSONB,
                tool_call_id TEXT,
                name         TEXT,
                created_at   TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_agent_messages_session ON agent_messages(session_id, seq)"
        ))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS agent_traces (
                id          BIGSERIAL PRIMARY KEY,
                session_id  TEXT NOT NULL,
                step        INTEGER,
                kind        TEXT,
                payload     JSONB,
                created_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """))


def _ensure_init():
    if _Session is None:
        raise RuntimeError("session DB not initialized; call init_db() first")


async def create_session(title: str | None, provider: str, model: str) -> str:
    _ensure_init()
    sid = uuid.uuid4().hex[:16]
    async with _Session() as s:  # type: ignore[misc]
        await s.execute(text(
            "INSERT INTO agent_sessions(id, title, llm_provider, llm_model) VALUES (:id, :t, :p, :m)"
        ), {"id": sid, "t": title or "新会话", "p": provider, "m": model})
        await s.commit()
    return sid


async def list_sessions(limit: int = 50) -> list[dict]:
    _ensure_init()
    async with _Session() as s:  # type: ignore[misc]
        r = await s.execute(text(
            "SELECT id, title, llm_provider, llm_model, created_at, updated_at "
            "FROM agent_sessions ORDER BY updated_at DESC LIMIT :n"
        ), {"n": limit})
        return [dict(row) for row in r.mappings().all()]


async def search_sessions(q: str, limit: int = 100) -> list[dict]:
    """Full-text search over session title and message content (case-insensitive)."""
    _ensure_init()
    pattern = f"%{q}%"
    async with _Session() as s:  # type: ignore[misc]
        r = await s.execute(text("""
            SELECT DISTINCT
                sess.id, sess.title, sess.llm_provider, sess.llm_model,
                sess.created_at, sess.updated_at
            FROM agent_sessions sess
            LEFT JOIN agent_messages msg ON msg.session_id = sess.id
            WHERE
                sess.title ILIKE :p
                OR msg.content ILIKE :p
            ORDER BY sess.updated_at DESC
            LIMIT :n
        """), {"p": pattern, "n": limit})
        return [dict(row) for row in r.mappings().all()]


async def append_message(session_id: str, msg: Message) -> None:
    _ensure_init()
    async with _Session() as s:  # type: ignore[misc]
        seq_row = (await s.execute(text(
            "SELECT COALESCE(MAX(seq), -1) + 1 AS n FROM agent_messages WHERE session_id = :sid"
        ), {"sid": session_id})).scalar_one()
        await s.execute(text("""
            INSERT INTO agent_messages(id, session_id, seq, role, content, tool_calls, tool_call_id, name)
            VALUES (:id, :sid, :seq, :role, :content, CAST(:tc AS JSONB), :tcid, :name)
        """), {
            "id": uuid.uuid4().hex,
            "sid": session_id,
            "seq": seq_row,
            "role": msg.role,
            "content": msg.content,
            "tc": json.dumps([tc.model_dump() for tc in msg.tool_calls]) if msg.tool_calls else None,
            "tcid": msg.tool_call_id,
            "name": msg.name,
        })
        await s.execute(text(
            "UPDATE agent_sessions SET updated_at = :u WHERE id = :sid"
        ), {"u": datetime.utcnow(), "sid": session_id})
        await s.commit()


async def load_messages(session_id: str) -> list[Message]:
    _ensure_init()
    async with _Session() as s:  # type: ignore[misc]
        r = await s.execute(text(
            "SELECT role, content, tool_calls, tool_call_id, name FROM agent_messages "
            "WHERE session_id = :sid ORDER BY seq ASC"
        ), {"sid": session_id})
        out: list[Message] = []
        for row in r.mappings().all():
            tc_raw = row["tool_calls"] or []
            tcs = [ToolCall(**tc) for tc in tc_raw] if tc_raw else []
            out.append(Message(
                role=row["role"], content=row["content"] or "",
                tool_calls=tcs, tool_call_id=row["tool_call_id"], name=row["name"],
            ))
        return out


async def log_trace(session_id: str, step: int, kind: str, payload: dict[str, Any]) -> None:
    _ensure_init()
    async with _Session() as s:  # type: ignore[misc]
        await s.execute(text(
            "INSERT INTO agent_traces(session_id, step, kind, payload) VALUES (:sid, :st, :k, CAST(:p AS JSONB))"
        ), {"sid": session_id, "st": step, "k": kind, "p": json.dumps(payload, ensure_ascii=False, default=str)})
        await s.commit()
