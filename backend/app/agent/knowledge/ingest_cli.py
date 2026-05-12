"""CLI to ingest documents into the knowledge base.

Usage:
  python -m agent.knowledge.ingest_cli file <path> --source research --title "..." [--code 600519]
  python -m agent.knowledge.ingest_cli text "raw content" --source note --title "..."
  python -m agent.knowledge.ingest_cli announcements --code 600519 --limit 20
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.agent.knowledge.embedder import get_default_embedder
from app.agent.knowledge.store import add_document, init_kb


async def _ingest_file(path: str, source: str, title: str | None, code: str | None, url: str | None):
    p = Path(path)
    title = title or p.stem
    content = p.read_text(encoding="utf-8")
    await init_kb()
    doc_id = await add_document(source=source, title=title, content=content,
                                code=code, url=url, embedder=get_default_embedder())
    print(f"doc_id={doc_id} chars={len(content)}")


async def _ingest_text(content: str, source: str, title: str, code: str | None):
    await init_kb()
    doc_id = await add_document(source=source, title=title, content=content,
                                code=code, embedder=get_default_embedder())
    print(f"doc_id={doc_id}")


async def _ingest_announcements(code: str, limit: int):
    """Pull recent A-share announcements via akshare."""
    import akshare as ak
    await init_kb()
    df = await asyncio.to_thread(ak.stock_notice_report, symbol="全部")
    df = df[df["代码"] == code.zfill(6)].head(limit)
    n = 0
    for _, row in df.iterrows():
        title = str(row.get("公告标题", ""))
        date = str(row.get("公告日期", ""))[:10] or None
        url = str(row.get("公告链接", ""))
        doc_id = await add_document(
            source="announcement", title=title,
            content=f"{title}\n\n来源: {url}\n日期: {date}",
            code=code, url=url, published_at=date,
            embedder=get_default_embedder(),
        )
        if doc_id:
            n += 1
    print(f"ingested {n} announcements for {code}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("file")
    f.add_argument("path"); f.add_argument("--source", default="note")
    f.add_argument("--title"); f.add_argument("--code"); f.add_argument("--url")

    t = sub.add_parser("text")
    t.add_argument("content"); t.add_argument("--source", default="note")
    t.add_argument("--title", required=True); t.add_argument("--code")

    a = sub.add_parser("announcements")
    a.add_argument("--code", required=True); a.add_argument("--limit", type=int, default=20)

    args = ap.parse_args()
    if args.cmd == "file":
        asyncio.run(_ingest_file(args.path, args.source, args.title, args.code, args.url))
    elif args.cmd == "text":
        asyncio.run(_ingest_text(args.content, args.source, args.title, args.code))
    elif args.cmd == "announcements":
        asyncio.run(_ingest_announcements(args.code, args.limit))


if __name__ == "__main__":
    main()
