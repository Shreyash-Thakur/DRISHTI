from datetime import timedelta

import pytest

from rca.config import Settings
from rca.graph import Graph
from rca.service.broadcaster import Broadcaster
from rca.service.correlator import Correlator
from rca.service.state import RcaState
from rca.service.ws_client import _handle_message

GRAPH = Graph.from_path("data/topology.json")
SETTINGS = Settings()


class _CapturingBroadcaster(Broadcaster):
    def __init__(self):
        super().__init__()
        self.published = []

    async def publish(self, incident):
        self.published.append(incident)


def _telemetry(node, sev="error"):
    return (
        '{"type": "telemetry", "batch": {"source": "sim", '
        '"sent_at": "2026-07-14T00:00:00Z", "interface_metrics": [], '
        '"tunnel_metrics": [], "events": [{"ts": "2026-07-14T00:00:00Z", '
        f'"node_id": "{node}", "severity": "{sev}", "event_type": "syslog", '
        '"message": "boom", "details": {"scenario": "link_degradation"}}]}}'
    )


async def _no_predictions():
    return None


@pytest.mark.anyio
async def test_handle_message_buffers_event_and_publishes_incident():
    state = RcaState(retention=timedelta(seconds=300))
    broadcaster = _CapturingBroadcaster()
    correlator = Correlator(GRAPH, SETTINGS, state, broadcaster)
    await _handle_message(_telemetry("p-core-1"), state, correlator, _no_predictions)
    assert len(state.current_events()) == 1
    assert len(broadcaster.published) == 1
    assert broadcaster.published[0]["root_cause"]["node_id"] == "p-core-1"


@pytest.mark.anyio
async def test_handle_message_ignores_non_telemetry():
    state = RcaState(retention=timedelta(seconds=300))
    broadcaster = _CapturingBroadcaster()
    correlator = Correlator(GRAPH, SETTINGS, state, broadcaster)
    await _handle_message('{"type": "other"}', state, correlator, _no_predictions)
    assert state.current_events() == []
    assert broadcaster.published == []
