"""SiliconFlow — OpenAI-compatible aggregator with FREE open-source models.

Free models (no charge ever):
  - Qwen/Qwen2.5-7B-Instruct
  - Qwen/Qwen2.5-Coder-7B-Instruct
  - THUDM/glm-4-9b-chat
  - internlm/internlm2_5-7b-chat
  - meta-llama/Meta-Llama-3.1-8B-Instruct

Get a free key at https://cloud.siliconflow.cn (sends 14¥ on signup).
"""
from __future__ import annotations

from app.agent.llm.openai_compat import OpenAICompatibleLLM


class SiliconFlowLLM(OpenAICompatibleLLM):
    pass
