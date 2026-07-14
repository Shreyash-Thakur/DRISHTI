"""Settings for the twin generator — reads the shared topology, writes lab
artifacts."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TWIN_")

    topology_path: Path = Path("data/topology.json")
    out_dir: Path = Path("twin/lab")
    node_image: str = "quay.io/frrouting/frr:9.1.0"
    mgmt_subnet: str = "172.20.20.0/24"
    asn: int = 65000


@lru_cache
def get_settings() -> Settings:
    return Settings()
