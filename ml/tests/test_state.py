from datetime import datetime, timedelta, timezone

from ml.service.state import RETENTION, LiveState


def test_add_metric_prunes_rows_older_than_retention():
    state = LiveState()
    base = datetime(2026, 7, 13, tzinfo=timezone.utc)
    state.add_metric({
        "ts": base, "node_id": "pe-east", "interface": "eth0",
        "utilization_pct": 10.0, "latency_ms": 5.0, "jitter_ms": 1.0, "packet_loss_pct": 0.0,
    })
    later = base + RETENTION + timedelta(seconds=1)
    state.add_metric({
        "ts": later, "node_id": "pe-east", "interface": "eth0",
        "utilization_pct": 20.0, "latency_ms": 5.0, "jitter_ms": 1.0, "packet_loss_pct": 0.0,
    })
    frame = state.metric_frame("pe-east", "eth0")
    assert len(frame) == 1
    assert frame.iloc[0]["utilization_pct"] == 20.0


def test_metric_frame_empty_for_unknown_node_interface():
    state = LiveState()
    frame = state.metric_frame("nowhere", "eth99")
    assert frame.empty
    assert list(frame.columns) == [
        "ts", "node_id", "interface", "utilization_pct", "latency_ms", "jitter_ms", "packet_loss_pct",
    ]


def test_set_and_get_prediction_roundtrip():
    state = LiveState()
    assert state.get_prediction("pe-east", "eth0") is None
    prediction = {"node_id": "pe-east", "interface": "eth0", "precursor_probability": 0.9,
                  "estimated_seconds_to_impact": 42.0}
    state.set_prediction("pe-east", "eth0", prediction)
    assert state.get_prediction("pe-east", "eth0") == prediction
    assert state.predictions_for_node("pe-east") == [prediction]
    assert state.all_predictions() == [prediction]
