"""pgvector-backed knowledge base for research reports, announcements, news, notes."""
from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.config import get_settings


_engine = None
_Session = None
_DIM = 1024  # default for text-embedding-v3 / bge-large


async def init_kb(dim: int = _DIM) -> None:
    """Create pgvector extension and knowledge tables. Idempotent.

    Each DDL runs in its own transaction so a failure (e.g. pgvector not installed)
    does not abort the others.
    """
    global _engine, _Session, _DIM
    _DIM = dim
    s = get_settings()
    _engine = create_async_engine(s.db_url, pool_pre_ping=True)
    _Session = async_sessionmaker(_engine, expire_on_commit=False)

    has_pgvector = True
    async with _engine.begin() as conn:
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception:
            has_pgvector = False

    vector_col = f"vector({dim})" if has_pgvector else "TEXT"

    statements = [
        f"""
        CREATE TABLE IF NOT EXISTS kb_documents (
            id          BIGSERIAL PRIMARY KEY,
            source      TEXT NOT NULL,
            title       TEXT,
            code        TEXT,
            url         TEXT,
            published_at DATE,
            content_hash TEXT UNIQUE,
            metadata    JSONB,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )""",
        f"""
        CREATE TABLE IF NOT EXISTS kb_chunks (
            id          BIGSERIAL PRIMARY KEY,
            doc_id      BIGINT REFERENCES kb_documents(id) ON DELETE CASCADE,
            chunk_idx   INTEGER NOT NULL,
            content     TEXT NOT NULL,
            embedding   {vector_col}
        )""",
        "CREATE INDEX IF NOT EXISTS ix_kb_chunks_doc ON kb_chunks(doc_id)",
        "CREATE INDEX IF NOT EXISTS ix_kb_chunks_content ON kb_chunks "
        "USING gin (to_tsvector('simple', content))",
    ]
    if has_pgvector:
        statements.append(
            "CREATE INDEX IF NOT EXISTS ix_kb_chunks_emb ON kb_chunks "
            "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
        )

    for stmt in statements:
        async with _engine.begin() as conn:
            try:
                await conn.execute(text(stmt))
            except Exception:
                # Index/extension may not be supported; non-fatal
                pass


def _ensure() -> None:
    if _Session is None:
        raise RuntimeError("KB not initialized; call init_kb() first")


def _chunk(text_content: str, size: int = 600, overlap: int = 80) -> list[str]:
    text_content = text_content.strip()
    if len(text_content) <= size:
        return [text_content] if text_content else []
    out, i = [], 0
    while i < len(text_content):
        out.append(text_content[i : i + size])
        i += size - overlap
    return out


def _hash(text_content: str) -> str:
    return hashlib.sha256(text_content.encode("utf-8")).hexdigest()


async def add_document(
    source: str,
    title: str,
    content: str,
    code: str | None = None,
    url: str | None = None,
    published_at: str | None = None,
    metadata: dict | None = None,
    embedder=None,
) -> int | None:
    """Add a document; chunk + embed + insert. Returns doc_id, or None if duplicate."""
    _ensure()
    import json
    h = _hash(content)
    chunks = _chunk(content)
    if not chunks:
        return None

    async with _Session() as s:  # type: ignore[misc]
        existing = (await s.execute(text("SELECT id FROM kb_documents WHERE content_hash = :h"),
                                    {"h": h})).scalar_one_or_none()
        if existing:
            return None

        doc_id = (await s.execute(text("""
            INSERT INTO kb_documents(source, title, code, url, published_at, content_hash, metadata)
            VALUES (:s, :t, :c, :u, :p, :h, CAST(:m AS JSONB)) RETURNING id
        """), {"s": source, "t": title, "c": code, "u": url, "p": published_at, "h": h,
               "m": json.dumps(metadata or {}, ensure_ascii=False)})).scalar_one()

        embeddings = await embedder(chunks) if embedder else [None] * len(chunks)
        for i, (chunk_text, emb) in enumerate(zip(chunks, embeddings, strict=True)):
            await s.execute(text("""
                INSERT INTO kb_chunks(doc_id, chunk_idx, content, embedding)
                VALUES (:d, :i, :c, CAST(:e AS vector))
            """), {"d": doc_id, "i": i, "c": chunk_text,
                   "e": _vec_str(emb) if emb else None})
        await s.commit()
        return int(doc_id)


def _vec_str(emb: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in emb) + "]"


async def search(
    query: str,
    top_k: int = 5,
    code_filter: str | None = None,
    embedder=None,
) -> list[dict[str, Any]]:
    """Hybrid: vector cosine + keyword tsvector union."""
    _ensure()
    results: list[dict] = []

    if embedder:
        try:
            qvec = (await embedder([query]))[0]
            sql = """
                SELECT c.content, d.title, d.source, d.code, d.url, d.published_at,
                       1 - (c.embedding <=> CAST(:q AS vector)) AS score
                FROM kb_chunks c JOIN kb_documents d ON d.id = c.doc_id
                WHERE c.embedding IS NOT NULL
                  AND (:code IS NULL OR d.code = :code)
                ORDER BY c.embedding <=> CAST(:q AS vector)
                LIMIT :k
            """
            async with _Session() as s:  # type: ignore[misc]
                r = await s.execute(text(sql), {"q": _vec_str(qvec), "code": code_filter, "k": top_k})
                results.extend(dict(row) for row in r.mappings().all())
        except Exception:
            pass

    # Always also do keyword fallback
    sql_kw = """
        SELECT c.content, d.title, d.source, d.code, d.url, d.published_at,
               ts_rank(to_tsvector('simple', c.content), plainto_tsquery('simple', :q)) AS score
        FROM kb_chunks c JOIN kb_documents d ON d.id = c.doc_id
        WHERE to_tsvector('simple', c.content) @@ plainto_tsquery('simple', :q)
          AND (:code IS NULL OR d.code = :code)
        ORDER BY score DESC LIMIT :k
    """
    async with _Session() as s:  # type: ignore[misc]
        r = await s.execute(text(sql_kw), {"q": query, "code": code_filter, "k": top_k})
        for row in r.mappings().all():
            d = dict(row)
            if not any(x["content"] == d["content"] for x in results):
                results.append(d)
    return results[:top_k]
