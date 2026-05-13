"""Agent configuration — reuses pivotlab's DATABASE_URL."""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # Database — reuse pivotlab's DATABASE_URL
    db_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://pivotlab:pivotlab@127.0.0.1:5433/pivotlab",
    )
    db_readonly_url: str | None = None

    # LLM
    llm_default_provider: Literal["qwen", "deepseek", "openai", "siliconflow", "doubao", "glm", "geekplus"] = "qwen"
    llm_default_model: str = "qwen-turbo"

    qwen_api_key: str | None = None
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    siliconflow_api_key: str | None = None
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    doubao_api_key: str | None = None
    doubao_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    doubao_model_id: str = ""  # user sets via env DOUBAO_MODEL_ID
    glm_api_key: str | None = None
    glm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    geekplus_api_key: str | None = None
    geekplus_base_url: str = "https://llm.geekplus.com/v1"

    # Agent
    agent_max_steps: int = 30
    agent_max_tokens_per_step: int = 4096
    session_context_limit: int = 32000

    # Sandbox
    bash_timeout_sec: int = 30
    bash_workdir: str = "/workspace"
    bash_allow_network: bool = True
    sql_query_timeout_sec: int = 15
    sql_max_rows: int = 500

    # Logging
    log_level: str = "INFO"
    log_json: bool = False

    @property
    def readonly_db_url(self) -> str:
        return self.db_readonly_url or self.db_url

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in (os.getenv("CORS_ORIGINS", "*")).split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
