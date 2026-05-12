"""Embedding providers (DashScope text-embedding-v3 by default; pluggable)."""
from __future__ import annotations

from collections.abc import Callable, Awaitable

import httpx

from app.agent.config import get_settings


EmbedderFn = Callable[[list[str]], Awaitable[list[list[float]]]]


async def dashscope_embedder(texts: list[str]) -> list[list[float]]:
    """DashScope (Aliyun) text-embedding-v3, 1024-d."""
    s = get_settings()
    if not s.qwen_api_key:
        raise RuntimeError("QWEN_API_KEY required for embeddings")
    url = f"{s.qwen_base_url.rstrip('/')}/embeddings"
    headers = {"Authorization": f"Bearer {s.qwen_api_key}", "Content-Type": "application/json"}
    payload = {"model": "text-embedding-v3", "input": texts, "encoding_format": "float"}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    return [item["embedding"] for item in data["data"]]


def get_default_embedder() -> EmbedderFn | None:
    s = get_settings()
    if s.qwen_api_key:
        return dashscope_embedder
    return None
