"""Shared types: messages, tool calls, LLM responses."""
from __future__ import annotations

import json
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator


Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("arguments", mode="before")
    @classmethod
    def _parse_arguments(cls, v: Any) -> dict[str, Any]:
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                v = {}
        return v if isinstance(v, dict) else {}


class Message(BaseModel):
    role: Role
    content: str = ""
    images: list[str] = Field(default_factory=list)  # base64 data-URIs for vision
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None  # for role="tool"
    name: str | None = None          # tool name when role="tool"


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    message: Message
    usage: Usage = Field(default_factory=Usage)
    finish_reason: str = "stop"
    raw: dict[str, Any] | None = None


class ToolSchema(BaseModel):
    """OpenAI-compatible function-calling schema."""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema
