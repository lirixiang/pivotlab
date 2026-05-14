"""Abstract base class for LLM providers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from app.agent.core.types import LLMResponse, Message, ToolCall, ToolSchema, Usage


@dataclass
class StreamDelta:
    """Incremental update from a streaming LLM response."""
    text_delta: str = ""
    thinking_delta: str = ""
    tool_call_delta: dict[str, Any] | None = None  # {index, id?, name?, args_delta?}
    finish_reason: str | None = None
    usage: Usage | None = None


class BaseLLM(ABC):
    """Unified LLM interface. All providers must implement this."""

    provider: str = "base"
    supports_streaming: bool = False

    def __init__(self, model: str, api_key: str, base_url: str, **kwargs):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.extra = kwargs

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamDelta]:
        """Default fallback: emit the final response as a single delta."""
        resp = await self.chat(messages, tools, temperature, max_tokens)
        yield StreamDelta(text_delta=resp.message.content,
                          finish_reason=resp.finish_reason, usage=resp.usage)

    @staticmethod
    def assemble(deltas: list[StreamDelta]) -> LLMResponse:
        """Reassemble a full LLMResponse from a list of streamed deltas."""
        import json
        import re
        import uuid

        text = "".join(d.text_delta for d in deltas if d.text_delta)
        tc_acc: dict[int, dict[str, Any]] = {}
        _seen_ids: dict[str, int] = {}  # tool_call_id -> tc_acc key
        _next_key = 0
        for d in deltas:
            if not d.tool_call_delta:
                continue
            tc_id = d.tool_call_delta.get("id")

            # If this delta has a new tool_call id, assign a fresh key
            if tc_id and tc_id not in _seen_ids:
                _seen_ids[tc_id] = _next_key
                _next_key += 1
                key = _seen_ids[tc_id]
            elif tc_id:
                key = _seen_ids[tc_id]
            else:
                # No id in delta (continuation chunk) — append to most recent slot
                key = _next_key - 1 if _next_key > 0 else 0

            slot = tc_acc.setdefault(key, {"id": "", "name": "", "args": ""})
            if tc_id:
                slot["id"] = tc_id
            if d.tool_call_delta.get("name"):
                slot["name"] = d.tool_call_delta["name"]
            if d.tool_call_delta.get("args_delta"):
                slot["args"] += d.tool_call_delta["args_delta"]

        tool_calls: list[ToolCall] = []
        for idx in sorted(tc_acc):
            slot = tc_acc[idx]
            raw_args = slot["args"] or ""
            name = slot["name"]
            base_id = slot["id"] or f"call_{uuid.uuid4().hex[:8]}"

            # Proxy may merge multiple tool_calls into one slot (all index=0, id only on first chunk).
            # Detect concatenated JSON objects like {"sql":"..."}{"sql":"..."} and split them.
            parts = _split_concat_json(raw_args)
            for i, part_str in enumerate(parts):
                try:
                    args = json.loads(part_str) if part_str else {}
                except json.JSONDecodeError:
                    args = {"_raw": part_str}
                tc_id = base_id if i == 0 else f"{base_id}_{i}"
                tool_calls.append(ToolCall(id=tc_id, name=name, arguments=args))

        usage = next((d.usage for d in reversed(deltas) if d.usage), Usage())
        finish = next((d.finish_reason for d in reversed(deltas) if d.finish_reason), "stop")
        return LLMResponse(
            message=Message(role="assistant", content=text, tool_calls=tool_calls),
            usage=usage, finish_reason=finish,
        )


def _split_concat_json(raw: str) -> list[str]:
    """Split concatenated JSON objects like '{"a":1}{"b":2}' into ['{"a":1}', '{"b":2}'].

    Handles nested braces correctly by tracking brace depth.
    Returns [raw] unchanged if it's a single valid object.
    """
    if not raw or raw[0] != "{":
        return [raw] if raw else [""]
    parts: list[str] = []
    depth = 0
    start = 0
    in_str = False
    escape = False
    for i, ch in enumerate(raw):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                parts.append(raw[start:i + 1])
                start = i + 1
    # If there's leftover (malformed), append it to last part
    if start < len(raw):
        if parts:
            parts[-1] += raw[start:]
        else:
            parts.append(raw[start:])
    return parts if parts else [raw]
