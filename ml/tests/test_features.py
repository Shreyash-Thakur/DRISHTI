import math

import pandas as pd

from ml.features import compute_features, feature_names


def test_compute_features_insufficient_history_returns_none():
    as_of = pd.Timestamp("2026-07-13T00:00:30Z")
    metric_history = pd.DataFrame({
        "ts": [pd.Timestamp("2026-07-13T00:00:29Z")],
        "node_id": ["pe-east"],
        "interface": ["TenGigE0/0/0"],
        "utilization_pct": [10.0],
        "latency_ms": [5.0],
        "jitter_ms": [1.0],
        "packet_loss_pct": [0.0],
    })
    event_history = pd.DataFrame(columns=["ts", "node_id", "severity"])
    assert compute_features(metric_history, event_history, as_of) is None


def test_compute_features_rising_utilization_has_positive_slope_and_full_feature_set():
    as_of = pd.Timestamp("2026-07-13T00:02:00Z")
    ts = pd.date_range(end=as_of, periods=25, freq="5s")  # 120s of 5s-spaced samples
    metric_history = pd.DataFrame({
        "ts": ts,
        "node_id": ["pe-east"] * 25,
        "interface": ["TenGigE0/0/0"] * 25,
        "utilization_pct": [10.0 + i for i in range(25)],  # steadily increasing
        "latency_ms": [5.0] * 25,
        "jitter_ms": [1.0] * 25,
        "packet_loss_pct": [0.0] * 25,
    })
    event_history = pd.DataFrame({
        "ts": [as_of - pd.Timedelta(seconds=10)],
        "node_id": ["pe-east"],
        "severity": ["warning"],
    })
    features = compute_features(metric_history, event_history, as_of)
    assert features is not None
    assert set(feature_names()) == set(features.keys())
    assert features["utilization_pct_120s_slope"] > 0
    assert features["utilization_pct_30s_mean"] > features["utilization_pct_120s_mean"]
    assert features["event_count_120s"] == 1.0
    assert features["event_severity_max_120s"] == 1.0  # "warning" rank


def test_compute_features_ignores_rows_after_as_of():
    as_of = pd.Timestamp("2026-07-13T00:01:00Z")
    ts = pd.date_range(end=as_of, periods=13, freq="5s")
    future_ts = ts.append(pd.DatetimeIndex([as_of + pd.Timedelta(seconds=5)]))
    metric_history = pd.DataFrame({
        "ts": future_ts,
        "node_id": ["pe-east"] * 14,
        "interface": ["TenGigE0/0/0"] * 14,
        "utilization_pct": [10.0] * 13 + [999.0],
        "latency_ms": [5.0] * 14,
        "jitter_ms": [1.0] * 14,
        "packet_loss_pct": [0.0] * 14,
    })
    event_history = pd.DataFrame(columns=["ts", "node_id", "severity"])
    features = compute_features(metric_history, event_history, as_of)
    assert features is not None
    assert features["utilization_pct_120s_max"] == 10.0


def test_compute_features_unmapped_severity_defaults_to_zero():
    """Unrecognized severity values should default to rank 0 instead of NaN."""
    as_of = pd.Timestamp("2026-07-13T00:02:00Z")
    ts = pd.date_range(end=as_of, periods=25, freq="5s")
    metric_history = pd.DataFrame({
        "ts": ts,
        "node_id": ["pe-east"] * 25,
        "interface": ["TenGigE0/0/0"] * 25,
        "utilization_pct": [10.0] * 25,
        "latency_ms": [5.0] * 25,
        "jitter_ms": [1.0] * 25,
        "packet_loss_pct": [0.0] * 25,
    })
    # Event with unknown severity that is not in SEVERITY_RANK
    event_history = pd.DataFrame({
        "ts": [as_of - pd.Timedelta(seconds=10)],
        "node_id": ["pe-east"],
        "severity": ["unknown_sev"],
    })
    features = compute_features(metric_history, event_history, as_of)
    assert features is not None
    # event_severity_max_120s should be a finite float, not NaN
    assert not math.isnan(features["event_severity_max_120s"])
    assert features["event_severity_max_120s"] == 0.0
