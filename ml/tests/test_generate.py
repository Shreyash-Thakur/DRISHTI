import pandas as pd

from ml.dataset.generate import build_training_set, node_interfaces


def test_node_interfaces_derives_from_links():
    topology = {
        "links": [
            {
                "id": "a__b",
                "a": {"node": "ce-site-a", "interface": "GigabitEthernet0/0"},
                "b": {"node": "pe-east", "interface": "GigabitEthernet0/1"},
            },
        ]
    }
    result = node_interfaces(topology)
    assert result["ce-site-a"] == ["GigabitEthernet0/0"]
    assert result["pe-east"] == ["GigabitEthernet0/1"]


def test_build_training_set_labels_ramp_rows_and_baseline_rows():
    started_at = "2026-07-13T00:00:00+00:00"
    manifest = [{
        "fault_id": "abc123",
        "scenario": "congestion_ramp",
        "node_id": "pe-east",
        "interface": "TenGigE0/0/0",
        "started_at": started_at,
        "ramp_seconds": 60,
        "hold_seconds": 30,
    }]
    # 30 samples, 5s apart, covering baseline (before started_at) through ramp+hold
    ts = pd.date_range(start="2026-07-12T23:59:00Z", periods=30, freq="5s")
    metrics = pd.DataFrame({
        "ts": ts,
        "node_id": ["pe-east"] * 30,
        "interface": ["TenGigE0/0/0"] * 30,
        "utilization_pct": [10.0] * 30,
        "latency_ms": [5.0] * 30,
        "jitter_ms": [1.0] * 30,
        "packet_loss_pct": [0.0] * 30,
    })
    events = pd.DataFrame(columns=["ts", "node_id", "severity"])

    training_set = build_training_set(manifest, metrics, events)

    # rows before started_at (with >=30s history) are baseline (is_precursor == 0)
    baseline_rows = training_set[training_set["ts"] < pd.Timestamp(started_at)]
    assert not baseline_rows.empty
    assert (baseline_rows["is_precursor"] == 0).all()
    assert baseline_rows["seconds_to_impact"].isna().all()

    # rows within [started_at, started_at + 60s] are ramp rows (is_precursor == 1)
    ramp_rows = training_set[
        (training_set["ts"] >= pd.Timestamp(started_at))
        & (training_set["ts"] <= pd.Timestamp(started_at) + pd.Timedelta(seconds=60))
    ]
    assert not ramp_rows.empty
    assert (ramp_rows["is_precursor"] == 1).all()
    assert (ramp_rows["seconds_to_impact"] >= 0).all()
