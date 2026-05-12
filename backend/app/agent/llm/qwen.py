"""Qwen (DashScope) — OpenAI-compatible mode with Qwen3 thinking support."""
from __future__ import annotations

from typing import Any

from app.agent.llm.openai_compat import OpenAICompatibleLLM


class QwenLLM(OpenAICompatibleLLM):
    provider = "qwen"

    def _is_thinking_model(self) -> bool:
        return "qwen3" in self.model.lower()

    def _payload(self, messages, tools, temperature, max_tokens, stream: bool) -> dict[str, Any]:
        p = super()._payload(messages, tools, temperature, max_tokens, stream)
        if self._is_thinking_model():
            p["enable_thinking"] = True
        return p
