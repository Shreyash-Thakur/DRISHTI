"""Settings for the copilot package — the LLM client, retriever, and service all
read the same Settings so URLs/model/paths stay in sync."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COPILOT_")

    port: int = 8400
    ollama_url: str = "http://localhost:11434"
    model: str = "mistral:7b"
    rca_url: str = "http://localhost:8300"
    runbooks_dir: Path = Path("data/runbooks")
    num_predict: int = 512
    num_ctx: int = 4096
    top_k: int = 3
    temperature: float = 0.2


@lru_cache
def get_settings() -> Settings:
    return Settings()
