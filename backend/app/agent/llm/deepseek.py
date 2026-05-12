"""DeepSeek — OpenAI-compatible."""
from app.agent.llm.openai_compat import OpenAICompatibleLLM


class DeepSeekLLM(OpenAICompatibleLLM):
    provider = "deepseek"
