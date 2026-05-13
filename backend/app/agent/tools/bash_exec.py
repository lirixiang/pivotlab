"""exec_bash: run a shell command in the agent's workspace.

Permission: 'confirm' — every call must be approved by the user (with optional
"always allow for this session" toggle handled at the agent layer).
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.agent.config import get_settings
from app.agent.tools.registry import registry


def _summarize(args: dict[str, Any]) -> str:
    cmd = (args.get("command") or "").strip()
    return cmd if len(cmd) <= 200 else cmd[:197] + "..."


@registry.register(
    name="exec_bash",
    description=(
        "Execute a bash command in the agent workspace. Use for: fetching live data via curl, "
        "running python scripts, exploring files, calling akshare/yfinance, etc. "
        "Output is truncated to ~10KB. Requires user approval before running."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The bash command to execute"},
            "cwd": {"type": "string", "description": "Working directory (defaults to BASH_WORKDIR)"},
            "timeout": {"type": "integer", "description": "Seconds; default from BASH_TIMEOUT_SEC"},
        },
        "required": ["command"],
    },
    permission="safe",
    summarize=_summarize,
)
async def exec_bash(args: dict[str, Any]) -> dict[str, Any]:
    s = get_settings()
    cmd = args["command"]
    cwd = args.get("cwd") or s.bash_workdir
    timeout = int(args.get("timeout") or s.bash_timeout_sec)

    import os
    if not os.path.isdir(cwd):
        cwd = s.bash_workdir
    if not os.path.isdir(cwd):
        cwd = "/tmp"

    proc = await asyncio.create_subprocess_shell(
        cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"error": f"timeout after {timeout}s", "exit_code": -1}

    def _trim(b: bytes, limit: int = 10_000) -> str:
        s = b.decode("utf-8", errors="replace")
        if len(s) > limit:
            return s[:limit] + f"\n...[truncated {len(s) - limit} chars]"
        return s

    return {
        "exit_code": proc.returncode,
        "stdout": _trim(stdout_b),
        "stderr": _trim(stderr_b),
        "cwd": cwd,
    }
