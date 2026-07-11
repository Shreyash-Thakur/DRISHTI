"""Business logic between routers and the repository. ML/copilot teammates can
import these functions directly instead of going through HTTP."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.repository import events_repo, telemetry_repo
from app.schemas import (
    EventsResponse,
    IngestResult,
    NodeMetricsResponse,
    TelemetryBatch,
)
from app.services import topology_service


class UnknownNodeError(ValueError):
    def __init__(self, unknown: set[str]) -> None:
        self.unknown = unknown
        super().__init__(f"unknown node ids: {sorted(unknown)}")


def ingest_batch(db_path: Path, topology_path: Path, batch: TelemetryBatch) -> IngestResult:
    known = topology_service.node_ids(topology_path)
    referenced = (
        {m.node_id for m in batch.interface_metrics}
        | {e.node_id for e in batch.events}
        | {m.src_node for m in batch.tunnel_metrics}
        | {m.dst_node for m in batch.tunnel_metrics}
    )
    unknown = referenced - known
    if unknown:
        raise UnknownNodeError(unknown)

    return IngestResult(
        accepted_interface_metrics=telemetry_repo.insert_interface_metrics(
            db_path, batch.interface_metrics
        ),
        accepted_tunnel_metrics=telemetry_repo.insert_tunnel_metrics(
            db_path, batch.tunnel_metrics
        ),
        accepted_events=events_repo.insert_events(db_path, batch.events),
    )


def get_node_metrics(
    db_path: Path,
    topology_path: Path,
    node_id: str,
    minutes: int,
    interface: str | None,
    limit: int,
) -> NodeMetricsResponse | None:
    if node_id not in topology_service.node_ids(topology_path):
        return None
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return NodeMetricsResponse(
        node_id=node_id,
        window_minutes=minutes,
        samples=telemetry_repo.query_interface_metrics(
            db_path, node_id, since, interface=interface, limit=limit
        ),
        tunnel_samples=telemetry_repo.query_tunnel_metrics(db_path, node_id, since, limit=limit),
    )


def get_events(
    db_path: Path,
    minutes: int,
    node_id: str | None,
    event_type: str | None,
    severity: str | None,
    limit: int,
) -> EventsResponse:
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    events = events_repo.query_events(
        db_path, since, node_id=node_id, event_type=event_type,
        severity=severity, limit=limit,
    )
    return EventsResponse(count=len(events), events=events)
