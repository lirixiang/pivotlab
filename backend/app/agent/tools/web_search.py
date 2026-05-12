"""Web search + URL fetch fallback tools.

Used when the local DB has no answer (e.g. fresh news, sector
explanation, latest filings, regulator announcements).
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx

from app.agent.tools.registry import registry

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _strip_html(html: str) -> str:
    # very lightweight tag stripper to avoid pulling in bs4 as hard dep
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    html = re.sub(r"&lt;", "<", html)
    html = re.sub(r"&gt;", ">", html)
    html = re.sub(r"&quot;", '"', html)
    html = re.sub(r"&#39;", "'", html)
    return re.sub(r"\s+", " ", html).strip()


@registry.register(
    name="web_search",
    description=(
        "Search the public web for fresh information when the local database "
        "has no answer (e.g. latest news, regulator announcements, sector "
        "explanations, recent IPOs). Returns top results with title, url and "
        "snippet. Use Chinese keywords for A-share topics."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search keywords"},
            "max_results": {
                "type": "integer", "default": 6, "minimum": 1, "maximum": 15,
            },
        },
        "required": ["query"],
    },
    permission="safe",
)
async def web_search(args: dict[str, Any]) -> dict[str, Any]:
    q = str(args["query"]).strip()
    n = int(args.get("max_results", 6))
    if not q:
        return {"results": [], "error": "empty query"}

    url = f"https://html.duckduckgo.com/html/?q={quote_plus(q)}"
    headers = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as cx:
        r = await cx.post(url, headers=headers, data={"q": q})
        if r.status_code != 200:
            return {"results": [], "error": f"http {r.status_code}"}
        html = r.text

    # parse DDG html result blocks
    results = []
    pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
        r'.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        re.S,
    )
    for m in pattern.finditer(html):
        href, title_html, snippet_html = m.group(1), m.group(2), m.group(3)
        # DDG wraps real url inside uddg= param
        m2 = re.search(r"uddg=([^&]+)", href)
        if m2:
            from urllib.parse import unquote
            href = unquote(m2.group(1))
        results.append({
            "title": _strip_html(title_html),
            "url": href,
            "snippet": _strip_html(snippet_html),
        })
        if len(results) >= n:
            break
    return {"query": q, "results": results, "count": len(results)}


@registry.register(
    name="fetch_url",
    description=(
        "Fetch a single web page and return its plain-text content (HTML "
        "tags stripped). Use after web_search to read the most relevant page. "
        "Output is truncated to ~6000 chars."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full http(s) URL"},
            "max_chars": {"type": "integer", "default": 6000},
        },
        "required": ["url"],
    },
    permission="safe",
)
async def fetch_url(args: dict[str, Any]) -> dict[str, Any]:
    url = str(args["url"]).strip()
    max_chars = int(args.get("max_chars", 6000))
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return {"error": "url must be http(s)"}
    headers = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as cx:
        r = await cx.get(url, headers=headers)
        if r.status_code >= 400:
            return {"url": url, "status": r.status_code, "error": "http error"}
        ctype = r.headers.get("content-type", "")
        body = r.text
    text = _strip_html(body) if "html" in ctype.lower() or body.lstrip().startswith("<") else body
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars] + "...[truncated]"
    return {
        "url": url, "status": r.status_code, "content_type": ctype,
        "text": text, "truncated": truncated,
    }
