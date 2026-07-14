"""Turns raw backend events into RCA-level Symptoms: one per graph anchor
(node / bgp_session / tunnel), aggregating severity with exponential time decay
so the correlator tracks the *active* front of a cascade, not stale noise."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}
SEVERITY_WEIGHT = {"warning": 1.0, "error": 3.0, "critical": 6.0}
MAX_SAMPLE_MESSAGES = 3


def parse_ts(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def rank_to_severity(rank: int) -> str:
    for name, value in SEVERITY_RANK.items():
        if value == rank:
            return name
    return "info"


@dataclass
class Symptom:
    anchor_type: str
    anchor_id: str
    node_id: str
    first_seen: datetime
    last_seen: datetime
    severity_max: str
    severity_weight: float
    event_count: int
    scenario: str | None = None
    sample_messages: list[str] = field(default_factory=list)
    precursor_probability: float | None = None
    estimated_seconds_to_impact: float | None = None

    def to_dict(self) -> dict:
        return {
            "anchor_type": self.anchor_type,
            "anchor_id": self.anchor_id,
            "node_id": self.node_id,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "severity_max": self.severity_max,
            "severity_weight": round(self.severity_weight, 4),
            "event_count": self.event_count,
            "scenario": self.scenario,
            "sample_messages": self.sample_messages,
            "precursor_probability": self.precursor_probability,
            "estimated_seconds_to_impact": self.estimated_seconds_to_impact,
        }


def anchor_for_event(event: dict) -> tuple[str, str]:
    etype = event.get("event_type")
    details = event.get("details") or {}
    if etype == "bgp" and details.get("session_id"):
        return "bgp_session", str(details["session_id"])
    if etype == "tunnel" and details.get("tunnel_id"):
        return "tunnel", str(details["tunnel_id"])
    return "node", event["node_id"]


def build_symptoms(
    events: list[dict],
    as_of: datetime,
    decay_tau: float,
    valid_nodes: set[str] | None = None,
) -> list[Symptom]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for ev in events:
        if SEVERITY_RANK.get(ev.get("severity", "info"), 0) < SEVERITY_RANK["warning"]:
            continue
        node_id = ev.get("node_id")
        if valid_nodes is not None and node_id not in valid_nodes:
            continue
        anchor = anchor_for_event(ev)
        grouped.setdefault(anchor, []).append(ev)
    return [
        _aggregate(anchor_type, anchor_id, evs, as_of, decay_tau)
        for (anchor_type, anchor_id), evs in grouped.items()
    ]


def _aggregate(
    anchor_type: str, anchor_id: str, events: list[dict], as_of: datetime, decay_tau: float,
) -> Symptom:
    ordered = sorted(events, key=lambda e: parse_ts(e["ts"]))
    times = [parse_ts(e["ts"]) for e in ordered]
    weight = 0.0
    max_rank = 0
    for ev, ts in zip(ordered, times):
        sev = ev.get("severity", "info")
        age = max(0.0, (as_of - ts).total_seconds())
        weight += SEVERITY_WEIGHT.get(sev, 0.0) * math.exp(-age / decay_tau)
        max_rank = max(max_rank, SEVERITY_RANK.get(sev, 0))
    scenario = next(
        ((e.get("details") or {}).get("scenario")
         for e in reversed(ordered) if (e.get("details") or {}).get("scenario")),
        None,
    )
    messages = [e.get("message", "") for e in reversed(ordered)][:MAX_SAMPLE_MESSAGES]
    return Symptom(
        anchor_type=anchor_type,
        anchor_id=anchor_id,
        node_id=ordered[-1]["node_id"],
        first_seen=times[0],
        last_seen=times[-1],
        severity_max=rank_to_severity(max_rank),
        severity_weight=weight,
        event_count=len(ordered),
        scenario=scenario,
        sample_messages=messages,
    )


def enrich_with_predictions(symptoms: list[Symptom], predictions: list[dict]) -> None:
    """Fold ml (:8200) predictions into node symptoms — highest-probability
    interface wins per node. Missing/None predictions leave symptoms untouched."""
    best_by_node: dict[str, dict] = {}
    for pred in predictions:
        prob = pred.get("precursor_probability")
        if prob is None:
            continue
        current = best_by_node.get(pred["node_id"])
        if current is None or prob > (current.get("precursor_probability") or -1.0):
            best_by_node[pred["node_id"]] = pred
    for symptom in symptoms:
        pred = best_by_node.get(symptom.node_id)
        if pred is not None:
            symptom.precursor_probability = pred.get("precursor_probability")
            symptom.estimated_seconds_to_impact = pred.get("estimated_seconds_to_impact")
