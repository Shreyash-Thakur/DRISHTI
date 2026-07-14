"""Runs a correlation pass over the current event buffer, diffs the result
against the previously-published incidents, and pushes opened/changed/resolved
incidents to WS subscribers."""
from __future__ import annotations

from datetime import datetime

from rca.config import Settings
from rca.correlate import correlate, incident_signature
from rca.graph import Graph
from rca.service.broadcaster import Broadcaster
from rca.service.state import RcaState
from rca.symptoms import build_symptoms, enrich_with_predictions


class Correlator:
    def __init__(self, graph: Graph, settings: Settings, state: RcaState, broadcaster: Broadcaster) -> None:
        self.graph = graph
        self.settings = settings
        self.state = state
        self.broadcaster = broadcaster

    def compute(self, now: datetime) -> list[dict]:
        symptoms = build_symptoms(
            self.state.current_events(), now, self.settings.decay_tau_seconds,
            valid_nodes=set(self.graph.nodes),
        )
        enrich_with_predictions(symptoms, self.state.predictions)
        node_estimates = {
            p["node_id"]: p.get("estimated_seconds_to_impact")
            for p in self.state.predictions
            if p.get("estimated_seconds_to_impact") is not None
        }
        incidents = correlate(symptoms, self.graph, self.settings, node_estimates)
        return [inc.to_dict() for inc in incidents]

    async def run_pass(self, now: datetime) -> None:
        new_incidents = self.compute(now)
        new_map = {inc["incident_id"]: inc for inc in new_incidents}
        old_map = self.state.incidents

        changed = [
            inc for iid, inc in new_map.items()
            if iid not in old_map or incident_signature(old_map[iid]) != incident_signature(inc)
        ]
        resolved_ids = [iid for iid in old_map if iid not in new_map]

        self.state.set_incidents(new_incidents)

        for inc in changed:
            await self.broadcaster.publish(inc)
        for iid in resolved_ids:
            resolved = {**old_map[iid], "status": "resolved", "updated_at": now.isoformat()}
            await self.broadcaster.publish(resolved)
