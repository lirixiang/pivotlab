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
        import uuid

        text = "".join(d.text_delta for d in deltas if d.text_delta)
        tc_acc: dict[int, dict[str, Any]] = {}
        for d in deltas:
            if not d.tool_call_delta:
                continue
            idx = d.tool_call_delta.get("index", 0)
            slot = tc_acc.setdefault(idx, {"id": "", "name": "", "args": ""})
            if d.tool_call_delta.get("id"):
                slot["id"] = d.tool_call_delta["id"]
            if d.tool_call_delta.get("name"):
                slot["name"] = d.tool_call_delta["name"]
            if d.tool_call_delta.get("args_delta"):
                slot["args"] += d.tool_call_delta["args_delta"]

        tool_calls: list[ToolCall] = []
        for idx in sorted(tc_acc):
            slot = tc_acc[idx]
            try:
                args = json.loads(slot["args"]) if slot["args"] else {}
            except json.JSONDecodeError:
                args = {"_raw": slot["args"]}
            tool_calls.append(ToolCall(
                id=slot["id"] or f"call_{uuid.uuid4().hex[:8]}",
                name=slot["name"], arguments=args,
            ))

        usage = next((d.usage for d in reversed(deltas) if d.usage), Usage())
        finish = next((d.finish_reason for d in reversed(deltas) if d.finish_reason), "stop")
        return LLMResponse(
            message=Message(role="assistant", content=text, tool_calls=tool_calls),
            usage=usage, finish_reason=finish,
        )
