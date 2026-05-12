"""MCP (Model Context Protocol) server exposing pivotlab-agent tools.

Implements MCP over stdio transport (the standard for Claude Desktop / Cursor).
Spec: https://modelcontextprotocol.io — JSON-RPC 2.0 messages over stdin/stdout.

Run:
    python -m agent.mcp.server

Tools with permission != 'safe' are excluded by default (set MCP_ALLOW_UNSAFE=1
to expose them; the consuming MCP client must then handle approval).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from app.agent.observability.logger import configure_logging, get_logger
from app.agent.tools import registry as _registry  # noqa: F401  -- ensures auto-import
from app.agent.tools.registry import registry

log = get_logger("agent.mcp")

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "pivotlab-agent", "version": "0.1.0"}


def _allowed_tools():
    allow_unsafe = os.getenv("MCP_ALLOW_UNSAFE", "0") == "1"
    for t in registry.all():
        if allow_unsafe or t.permission == "safe":
            yield t


async def _handle(req: dict[str, Any]) -> dict[str, Any] | None:
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}

    def ok(result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        })

    if method in ("notifications/initialized", "initialized"):
        return None  # notification, no response

    if method == "tools/list":
        return ok({
            "tools": [
                {"name": t.name, "description": t.description, "inputSchema": t.parameters}
                for t in _allowed_tools()
            ],
        })

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = registry.get(name)
        if not tool:
            return err(-32601, f"Tool not found: {name}")
        if tool.permission != "safe" and os.getenv("MCP_ALLOW_UNSAFE", "0") != "1":
            return err(-32603, f"Tool '{name}' requires approval; set MCP_ALLOW_UNSAFE=1 to expose")
        fn = registry.func(name)
        try:
            result = await fn(args)
        except Exception as e:  # noqa: BLE001
            log.exception("tool_failed", tool=name)
            return err(-32603, f"{type(e).__name__}: {e}")
        return ok({
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}],
            "isError": False,
        })

    if method == "ping":
        return ok({})

    return err(-32601, f"Method not found: {method}")


async def _serve_stdio() -> None:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    writer_transport, writer_protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, sys.stdout)
    writer = asyncio.StreamWriter(writer_transport, writer_protocol, None, loop)

    while True:
        line = await reader.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        resp = await _handle(req)
        if resp is not None:
            writer.write((json.dumps(resp, ensure_ascii=False) + "\n").encode())
            await writer.drain()


def main():
    configure_logging()
    asyncio.run(_serve_stdio())


if __name__ == "__main__":
    main()
