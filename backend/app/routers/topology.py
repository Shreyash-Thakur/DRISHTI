from typing import Any

from fastapi import APIRouter

from app.config import get_settings
from app.services import topology_service

router = APIRouter(tags=["topology"])


@router.get("/topology")
def get_topology() -> dict[str, Any]:
    return topology_service.get_topology(get_settings().topology_path)
