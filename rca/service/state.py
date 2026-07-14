"""In-memory rolling event buffer + latest ml predictions + current incidents.
Single instance per process; the WS client is the only writer and the routes are
read-only, both on one asyncio loop, so no locking is needed (mirrors
ml/service/state.py)."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

from rca.symptoms import parse_ts


class RcaState:
    def __init__(self, retention: timedelta) -> None:
        self.retention = retention
        self.events: deque[dict] = deque()
        self.predictions: list[dict] = []
        self.incidents: dict[str, dict] = {}

    def add_event(self, event: dict, now: datetime) -> None:
        self.events.append(event)
        cutoff = now - self.retention
        while self.events and parse_ts(self.events[0]["ts"]) < cutoff:
            self.events.popleft()

    def current_events(self) -> list[dict]:
        return list(self.events)

    def set_incidents(self, incidents: list[dict]) -> None:
        self.incidents = {inc["incident_id"]: inc for inc in incidents}

    def all_incidents(self) -> list[dict]:
        return list(self.incidents.values())

    def get_incident(self, incident_id: str) -> dict | None:
        return self.incidents.get(incident_id)
