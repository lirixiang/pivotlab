"""read_file / write_file: file I/O tools for the agent."""
from __future__ import annotations

import os
from typing import Any

from app.agent.tools.registry import registry

# Restrict file ops to project directory for safety
_ALLOWED_ROOT = "/app/backend"


def _safe_path(path: str) -> str | None:
    """Resolve path and ensure it's under allowed root."""
    resolved = os.path.realpath(path)
    if not resolved.startswith(_ALLOWED_ROOT):
        return None
    return resolved


@registry.register(
    name="read_file",
    description=(
        "Read a file from the project. Use to inspect source code, configs, data files, "
        "SQL schemas, service modules, etc. Returns file content (truncated to 30KB). "
        "Path is relative to /app/backend or absolute."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path (relative to /app/backend or absolute)"},
            "start_line": {"type": "integer", "description": "Start line (1-based, optional)"},
            "end_line": {"type": "integer", "description": "End line (1-based, optional)"},
        },
        "required": ["path"],
    },
    permission="safe",
)
async def read_file(args: dict[str, Any]) -> dict[str, Any]:
    path = args["path"]
    if not os.path.isabs(path):
        path = os.path.join(_ALLOWED_ROOT, path)

    resolved = _safe_path(path)
    if not resolved:
        return {"error": f"path not allowed (must be under {_ALLOWED_ROOT})"}

    if not os.path.isfile(resolved):
        # If it's a directory, list contents
        if os.path.isdir(resolved):
            entries = sorted(os.listdir(resolved))[:100]
            return {"type": "directory", "path": resolved, "entries": entries}
        return {"error": f"file not found: {resolved}"}

    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return {"error": str(e)}

    start = int(args.get("start_line") or 1) - 1
    end = int(args.get("end_line") or len(lines))
    start = max(0, start)
    end = min(len(lines), end)

    content = "".join(lines[start:end])
    if len(content) > 30000:
        content = content[:30000] + "\n... [truncated]"

    return {
        "path": resolved,
        "total_lines": len(lines),
        "showing": f"{start+1}-{end}",
        "content": content,
    }


@registry.register(
    name="write_file",
    description=(
        "Write content to a file. Use for creating scripts, saving trade plans, "
        "writing configs, etc. Creates parent directories if needed. "
        "Path is relative to /app/backend or absolute (must be under /app/backend)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path (relative to /app/backend or absolute)"},
            "content": {"type": "string", "description": "File content to write"},
            "append": {"type": "boolean", "description": "Append instead of overwrite (default: false)"},
        },
        "required": ["path", "content"],
    },
    permission="safe",
)
async def write_file(args: dict[str, Any]) -> dict[str, Any]:
    path = args["path"]
    if not os.path.isabs(path):
        path = os.path.join(_ALLOWED_ROOT, path)

    resolved = _safe_path(path)
    if not resolved:
        return {"error": f"path not allowed (must be under {_ALLOWED_ROOT})"}

    content = args["content"]
    append = bool(args.get("append", False))

    try:
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        mode = "a" if append else "w"
        with open(resolved, mode, encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        return {"error": str(e)}

    size = os.path.getsize(resolved)
    return {"path": resolved, "bytes_written": len(content.encode("utf-8")), "total_size": size, "mode": "append" if append else "write"}
