"""HTTP transport for MCP — mounts a /mcp endpoint on the FastAPI app.

Usage from API server:
    from app.agent.mcp.http import mount_mcp; mount_mcp(app)

Clients POST JSON-RPC requests to /mcp and get JSON responses (one shot).
For full MCP-over-HTTP streaming, use stdio (more reliable today).
"""
from __future__ import annotations

from fastapi import FastAPI, Request

from app.agent.mcp.server import _handle


def mount_mcp(app: FastAPI, path: str = "/mcp") -> None:
    @app.post(path)
    async def mcp_endpoint(req: Request):
        body = await req.json()
        resp = await _handle(body)
        return resp or {"jsonrpc": "2.0", "id": body.get("id"), "result": {}}
