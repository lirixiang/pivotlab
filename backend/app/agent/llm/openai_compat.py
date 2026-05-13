"""OpenAI-compatible LLM client (Qwen, DeepSeek, OpenAI, Ollama, vLLM)."""
from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.agent.core.types import LLMResponse, Message, ToolCall, ToolSchema, Usage
from app.agent.llm.base import BaseLLM, StreamDelta


def _msg_to_dict(m: Message) -> dict[str, Any]:
    d: dict[str, Any] = {"role": m.role}
    if m.role == "tool":
        d["tool_call_id"] = m.tool_call_id
        d["content"] = m.content or "ok"
        if m.name:
            d["name"] = m.name
        return d
    if m.tool_calls:
        d["tool_calls"] = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
            for tc in m.tool_calls
        ]
        # Claude rejects empty content when tool_calls present; omit or use null
        if m.content:
            d["content"] = m.content
    else:
        d["content"] = m.content or ""
    return d


def _tool_to_dict(t: ToolSchema) -> dict[str, Any]:
    return {"type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}


class OpenAICompatibleLLM(BaseLLM):
    provider = "openai"
    supports_streaming = True

    def _payload(self, messages, tools, temperature, max_tokens, stream: bool) -> dict[str, Any]:
        p: dict[str, Any] = {
            "model": self.model,
            "messages": [_msg_to_dict(m) for m in messages],
            "temperature": temperature,
        }
        if max_tokens:
            p["max_tokens"] = max_tokens
        if tools:
            p["tools"] = [_tool_to_dict(t) for t in tools]
            p["tool_choice"] = "auto"
        if stream:
            p["stream"] = True
            p["stream_options"] = {"include_usage": True}
        return p

    async def chat(self, messages, tools=None, temperature=0.3, max_tokens=None) -> LLMResponse:
        payload = self._payload(messages, tools, temperature, max_tokens, stream=False)
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()

        choice = data["choices"][0]
        msg_data = choice["message"]
        tool_calls: list[ToolCall] = []
        for tc in msg_data.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.get("arguments")}
            tool_calls.append(ToolCall(id=tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                                       name=fn["name"], arguments=args))
        u = data.get("usage") or {}
        return LLMResponse(
            message=Message(role="assistant", content=msg_data.get("content") or "", tool_calls=tool_calls),
            usage=Usage(prompt_tokens=u.get("prompt_tokens", 0),
                        completion_tokens=u.get("completion_tokens", 0),
                        total_tokens=u.get("total_tokens", 0)),
            finish_reason=choice.get("finish_reason", "stop"),
            raw=data,
        )

    async def stream(self, messages, tools=None, temperature=0.3, max_tokens=None) -> AsyncIterator[StreamDelta]:
        payload = self._payload(messages, tools, temperature, max_tokens, stream=True)
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as r:
                if r.status_code >= 400:
                    body = await r.aread()
                    import logging
                    logging.getLogger(__name__).error("LLM API error %s: %s", r.status_code, body.decode(errors="replace"))
                    r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue

                    # final usage-only chunk
                    if obj.get("usage") and not obj.get("choices"):
                        u = obj["usage"]
                        yield StreamDelta(usage=Usage(
                            prompt_tokens=u.get("prompt_tokens", 0),
                            completion_tokens=u.get("completion_tokens", 0),
                            total_tokens=u.get("total_tokens", 0),
                        ))
                        continue

                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    ch = choices[0]
                    delta = ch.get("delta") or {}
                    finish = ch.get("finish_reason")

                    # Thinking/reasoning content
                    # - Qwen3 / DeepSeek: reasoning_content
                    # - OpenAI o-series / Claude via gateways: reasoning (string or dict)
                    # - Anthropic-style proxied as: thinking
                    thinking = (
                        delta.get("reasoning_content")
                        or delta.get("thinking")
                        or ""
                    )
                    if not thinking:
                        r = delta.get("reasoning")
                        if isinstance(r, str):
                            thinking = r
                        elif isinstance(r, dict):
                            thinking = r.get("content") or r.get("text") or ""
                    if thinking:
                        yield StreamDelta(thinking_delta=thinking)

                    text = delta.get("content") or ""
                    if text:
                        yield StreamDelta(text_delta=text)

                    for tc in (delta.get("tool_calls") or []):
                        yield StreamDelta(tool_call_delta={
                            "index": tc.get("index", 0),
                            "id": tc.get("id"),
                            "name": (tc.get("function") or {}).get("name"),
                            "args_delta": (tc.get("function") or {}).get("arguments"),
                        })

                    if finish:
                        u = obj.get("usage")
                        usage = None
                        if u:
                            usage = Usage(prompt_tokens=u.get("prompt_tokens", 0),
                                          completion_tokens=u.get("completion_tokens", 0),
                                          total_tokens=u.get("total_tokens", 0))
                        yield StreamDelta(finish_reason=finish, usage=usage)
