from fastapi import APIRouter, Query

from app.config import get_settings
from app.schemas import EventsResponse, EventType, Severity
from app.services import telemetry_service

router = APIRouter(tags=["events"])


@router.get("/events", response_model=EventsResponse)
def get_events(
    minutes: int = Query(default=60, ge=1, le=7 * 24 * 60),
    node_id: str | None = Query(default=None),
    event_type: EventType | None = Query(default=None),
    severity: Severity | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=5000),
) -> EventsResponse:
    settings = get_settings()
    return telemetry_service.get_events(
        settings.db_path, minutes=minutes, node_id=node_id,
        event_type=event_type, severity=severity, limit=limit,
    )
