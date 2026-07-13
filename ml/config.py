"""Settings for the ml package — dataset generation, training, and the live
service all read the same Settings so paths/URLs stay in sync."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ML_")

    port: int = 8200
    backend_http_url: str = "http://localhost:8000"
    backend_ws_url: str = "ws://localhost:8000/ws/live"
    simulator_url: str = "http://localhost:8100"
    db_path: Path = Path("data/drishti.db")
    topology_path: Path = Path("data/topology.json")
    dataset_dir: Path = Path("ml/dataset")
    model_dir: Path = Path("ml/models")
    precursor_threshold: float = 0.5


@lru_cache
def get_settings() -> Settings:
    return Settings()
