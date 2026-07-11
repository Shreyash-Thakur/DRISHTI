"""Pydantic models shared by the ingest API and query responses."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Severity = Literal["info", "warning", "error", "critical"]
EventType = Literal["bgp", "ospf", "syslog", "tunnel", "system"]
TunnelState = Literal["up", "degraded", "down"]


class InterfaceMetric(BaseModel):
    ts: datetime
    node_id: str
    interface: str
    utilization_pct: float = Field(ge=0, le=100)
    latency_ms: float = Field(ge=0)
    jitter_ms: float = Field(ge=0)
    packet_loss_pct: float = Field(ge=0, le=100)


class TunnelMetric(BaseModel):
    ts: datetime
    tunnel_id: str
    src_node: str
    dst_node: str
    state: TunnelState
    throughput_mbps: float = Field(ge=0)
    latency_ms: float = Field(ge=0)
    encap_errors: int = Field(ge=0)


class NetworkEvent(BaseModel):
    ts: datetime
    node_id: str
    severity: Severity
    event_type: EventType
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class TelemetryBatch(BaseModel):
    """Envelope the simulator POSTs to /telemetry/ingest every tick."""

    source: str
    sent_at: datetime
    interface_metrics: list[InterfaceMetric] = Field(default_factory=list)
    tunnel_metrics: list[TunnelMetric] = Field(default_factory=list)
    events: list[NetworkEvent] = Field(default_factory=list)


class IngestResult(BaseModel):
    accepted_interface_metrics: int
    accepted_tunnel_metrics: int
    accepted_events: int


class NodeMetricsResponse(BaseModel):
    node_id: str
    window_minutes: int
    samples: list[InterfaceMetric]
    tunnel_samples: list[TunnelMetric]


class EventsResponse(BaseModel):
    count: int
    events: list[NetworkEvent]
