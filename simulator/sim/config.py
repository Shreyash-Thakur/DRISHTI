"""Simulator configuration, loaded from environment / .env with the SIM_ prefix."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIM_", env_file=".env", extra="ignore")

    backend_url: str = "http://localhost:8000"
    topology_path: Path = Path("./data/topology.json")
    interval_seconds: float = 5.0
    host: str = "0.0.0.0"
    api_port: int = 8100
    seed: int = 42


@lru_cache
def get_settings() -> Settings:
    return Settings()
