"""LLM factory: unified LLM management for agent + pivotlab services.

All LLM providers are configured here — agent chat, LLM精选, and any
future LLM needs share this single factory.
"""
from __future__ import annotations

from app.agent.config import get_settings
from app.agent.llm.base import BaseLLM
from app.agent.llm.deepseek import DeepSeekLLM
from app.agent.llm.openai_compat import OpenAICompatibleLLM
from app.agent.llm.qwen import QwenLLM
from app.agent.llm.siliconflow import SiliconFlowLLM


def build_llm(provider: str | None = None, model: str | None = None) -> BaseLLM:
    s = get_settings()
    provider = (provider or s.llm_default_provider).lower()
    model = model or s.llm_default_model

    if provider == "qwen":
        if not s.qwen_api_key:
            raise RuntimeError("QWEN_API_KEY not configured")
        return QwenLLM(model=model, api_key=s.qwen_api_key, base_url=s.qwen_base_url)

    if provider in ("qwen_flash",):
        # Alias for free Qwen models
        if not s.qwen_api_key:
            raise RuntimeError("QWEN_API_KEY not configured")
        return QwenLLM(model=model or "qwen3.6-flash", api_key=s.qwen_api_key, base_url=s.qwen_base_url)

    if provider == "deepseek":
        if not s.deepseek_api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not configured")
        return DeepSeekLLM(model=model, api_key=s.deepseek_api_key, base_url=s.deepseek_base_url)

    if provider == "openai":
        if not s.openai_api_key or not s.openai_base_url:
            raise RuntimeError("OPENAI_API_KEY / OPENAI_BASE_URL not configured")
        return OpenAICompatibleLLM(model=model, api_key=s.openai_api_key, base_url=s.openai_base_url)

    if provider == "siliconflow":
        if not s.siliconflow_api_key:
            raise RuntimeError("SILICONFLOW_API_KEY not configured (free key at https://cloud.siliconflow.cn)")
        return SiliconFlowLLM(model=model, api_key=s.siliconflow_api_key, base_url=s.siliconflow_base_url)

    if provider == "doubao":
        if not s.doubao_api_key:
            raise RuntimeError("DOUBAO_API_KEY not configured")
        final_model = model or s.doubao_model_id
        if not final_model:
            raise RuntimeError("DOUBAO_MODEL_ID not configured")
        return OpenAICompatibleLLM(model=final_model, api_key=s.doubao_api_key, base_url=s.doubao_base_url)

    if provider == "glm":
        if not s.glm_api_key:
            raise RuntimeError("GLM_API_KEY not configured")
        return OpenAICompatibleLLM(model=model or "glm-4-plus", api_key=s.glm_api_key, base_url=s.glm_base_url)

    if provider == "geekplus":
        if not s.geekplus_api_key:
            raise RuntimeError("GEEKPLUS_API_KEY not configured")
        return OpenAICompatibleLLM(model=model or "claude-sonnet-4-6", api_key=s.geekplus_api_key, base_url=s.geekplus_base_url)

    raise ValueError(f"Unknown LLM provider: {provider}")


def list_available_providers() -> list[dict]:
    """For UI: list which providers are configured."""
    s = get_settings()
    return [
        {"provider": "qwen", "available": bool(s.qwen_api_key),
         "default_model": "qwen-turbo",
         "models": ["qwen-turbo", "qwen-plus", "qwen-max", "qwen3-max", "qwen3.6-flash"]},
        {"provider": "deepseek", "available": bool(s.deepseek_api_key),
         "default_model": "deepseek-chat",
         "models": ["deepseek-chat", "deepseek-reasoner"]},
        {"provider": "doubao", "available": bool(s.doubao_api_key),
         "default_model": s.doubao_model_id or "doubao-pro",
         "models": [s.doubao_model_id] if s.doubao_model_id else ["doubao-pro"]},
        {"provider": "glm", "available": bool(s.glm_api_key),
         "default_model": "glm-4-plus",
         "models": ["glm-4-plus", "glm-4-flash"]},
        {"provider": "siliconflow", "available": bool(s.siliconflow_api_key),
         "default_model": "Qwen/Qwen2.5-7B-Instruct",
         "models": [
             "Qwen/Qwen2.5-7B-Instruct",
             "Qwen/Qwen2.5-Coder-7B-Instruct",
             "THUDM/glm-4-9b-chat",
             "internlm/internlm2_5-7b-chat",
             "meta-llama/Meta-Llama-3.1-8B-Instruct",
         ]},
        {"provider": "openai", "available": bool(s.openai_api_key and s.openai_base_url),
         "default_model": "gpt-4o-mini", "models": []},
        {"provider": "geekplus", "available": bool(s.geekplus_api_key),
         "default_model": "claude-sonnet-4-6",
         "models": ["claude-sonnet-4-6", "claude-opus-4-6", "gpt-5.5", "gpt-5.3-codex", "claude-haiku-4-5-20251001"]},
    ]
