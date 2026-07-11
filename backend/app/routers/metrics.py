from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.schemas import NodeMetricsResponse
from app.services import telemetry_service

router = APIRouter(tags=["metrics"])


@router.get("/metrics/{node_id}", response_model=NodeMetricsResponse)
def get_node_metrics(
    node_id: str,
    minutes: int = Query(default=15, ge=1, le=24 * 60),
    interface: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=10_000),
) -> NodeMetricsResponse:
    settings = get_settings()
    result = telemetry_service.get_node_metrics(
        settings.db_path, settings.topology_path, node_id,
        minutes=minutes, interface=interface, limit=limit,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"unknown node: {node_id}")
    return result
