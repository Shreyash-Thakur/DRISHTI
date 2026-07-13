import pandas as pd
import pytest

from ml.service.state import LiveState
from ml.service.ws_client import _handle_message


class _FakeBroadcast:
    def __init__(self):
        self.published = []

    async def publish(self, prediction):
        self.published.append(prediction)


class _FakePredictor:
    def predict(self, node_id, interface, metric_history, event_history, as_of):
        return {
            "node_id": node_id, "interface": interface,
            "precursor_probability": 0.7, "estimated_seconds_to_impact": 30.0,
        }


@pytest.mark.anyio
async def test_handle_message_updates_buffers_and_publishes_prediction():
    state = LiveState()
    predictor = _FakePredictor()
    broadcast = _FakeBroadcast()
    raw = (
        '{"type": "telemetry", "batch": {"source": "sim", "sent_at": "2026-07-13T00:00:00Z", '
        '"interface_metrics": [{"ts": "2026-07-13T00:00:00Z", "node_id": "pe-east", '
        '"interface": "eth0", "utilization_pct": 10.0, "latency_ms": 5.0, "jitter_ms": 1.0, '
        '"packet_loss_pct": 0.0}], "tunnel_metrics": [], "events": []}}'
    )
    await _handle_message(raw, state, predictor, broadcast)

    frame = state.metric_frame("pe-east", "eth0")
    assert len(frame) == 1
    assert broadcast.published == [{
        "node_id": "pe-east", "interface": "eth0",
        "precursor_probability": 0.7, "estimated_seconds_to_impact": 30.0,
    }]
    assert state.get_prediction("pe-east", "eth0")["precursor_probability"] == 0.7


@pytest.mark.anyio
async def test_handle_message_ignores_non_telemetry_messages():
    state = LiveState()
    broadcast = _FakeBroadcast()
    await _handle_message('{"type": "other"}', state, _FakePredictor(), broadcast)
    assert state.all_predictions() == []
    assert broadcast.published == []
