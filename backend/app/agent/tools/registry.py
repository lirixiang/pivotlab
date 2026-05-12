"""Tool registry + permission model.

Permission levels (Claude-style):
  - safe:      auto-approved
  - confirm:   requires user approval per call; user can grant "always allow this tool" within the session
  - dangerous: always requires explicit per-call approval (no always-allow)
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal
from pydantic import BaseModel

from app.agent.core.types import ToolSchema


PermissionLevel = Literal["safe", "confirm", "dangerous"]
ToolFn = Callable[[dict[str, Any]], Awaitable[Any]]


class Tool(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    permission: PermissionLevel = "safe"
    # human-readable summary of what THIS particular call will do (for approval UI)
    summarize: Callable[[dict[str, Any]], str] | None = None

    model_config = {"arbitrary_types_allowed": True}


class _Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._funcs: dict[str, ToolFn] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        permission: PermissionLevel = "safe",
        summarize: Callable[[dict[str, Any]], str] | None = None,
    ):
        def deco(fn: ToolFn) -> ToolFn:
            self._tools[name] = Tool(
                name=name, description=description, parameters=parameters,
                permission=permission, summarize=summarize,
            )
            self._funcs[name] = fn
            return fn
        return deco

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def func(self, name: str) -> ToolFn | None:
        return self._funcs.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[ToolSchema]:
        return [
            ToolSchema(name=t.name, description=t.description, parameters=t.parameters)
            for t in self._tools.values()
        ]


registry = _Registry()
