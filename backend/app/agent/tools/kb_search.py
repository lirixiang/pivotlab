"""kb_search tool: retrieve relevant snippets from the knowledge base."""
from __future__ import annotations

from typing import Any

from app.agent.knowledge.embedder import get_default_embedder
from app.agent.knowledge.store import search
from app.agent.tools.registry import registry


@registry.register(
    name="kb_search",
    description=(
        "Search the internal knowledge base (research reports, company announcements, "
        "news, personal notes) using hybrid vector + keyword search. Use when the user "
        "asks about specific company events, analyst views, or recent news context."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural-language search query"},
            "code": {"type": "string", "description": "Optional 6-digit code to scope to a stock"},
            "top_k": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    },
    permission="safe",
)
async def kb_search(args: dict[str, Any]) -> dict[str, Any]:
    query = args["query"]
    code = args.get("code")
    k = int(args.get("top_k") or 5)
    try:
        hits = await search(query=query, top_k=k, code_filter=code, embedder=get_default_embedder())
    except RuntimeError as e:
        return {"error": str(e), "hits": []}
    return {
        "query": query,
        "hits": [
            {
                "title": h.get("title"),
                "source": h.get("source"),
                "code": h.get("code"),
                "published_at": str(h.get("published_at") or ""),
                "score": float(h.get("score") or 0),
                "snippet": (h.get("content") or "")[:500],
                "url": h.get("url"),
            }
            for h in hits
        ],
    }
