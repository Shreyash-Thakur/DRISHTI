"""Settings for the rca package — the graph loader, correlator, and live
service all read the same Settings so paths/URLs/thresholds stay in sync."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RCA_")

    port: int = 8300
    backend_ws_url: str = "ws://localhost:8000/ws/live"
    backend_http_url: str = "http://localhost:8000"
    ml_url: str = "http://localhost:8200"
    topology_path: Path = Path("data/topology.json")

    temporal_window_seconds: float = 120.0
    decay_tau_seconds: float = 120.0
    min_symptom_weight: float = 0.1
    cascade_max_hops: int = 2
    w_earliest: float = 0.4
    w_central: float = 0.3
    w_reach: float = 0.3


@lru_cache
def get_settings() -> Settings:
    return Settings()
