"""Qwen (DashScope) — OpenAI-compatible mode."""
from app.agent.llm.openai_compat import OpenAICompatibleLLM


class QwenLLM(OpenAICompatibleLLM):
    provider = "qwen"
