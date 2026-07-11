"""Backend configuration, loaded from environment / .env with the DRISHTI_ prefix."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DRISHTI_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    db_path: Path = Path("./data/drishti.db")
    topology_path: Path = Path("./data/topology.json")


@lru_cache
def get_settings() -> Settings:
    return Settings()
