"""Rolling-window feature engineering shared by dataset generation (Task 4),
training (Task 5), and the live predictor (Task 6). Any change here requires
retraining — ml/service/predictor.py checks the saved feature list matches."""
from __future__ import annotations

import numpy as np
import pandas as pd

METRIC_COLS = ["utilization_pct", "latency_ms", "jitter_ms", "packet_loss_pct"]
WINDOW_SECONDS = (30, 60, 120)
STATS = ("mean", "std", "min", "max", "slope")
SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}


def feature_names() -> list[str]:
    names = []
    for metric in METRIC_COLS:
        for window in WINDOW_SECONDS:
            for stat in STATS:
                names.append(f"{metric}_{window}s_{stat}")
    names.append("event_count_120s")
    names.append("event_severity_max_120s")
    return names


def _slope(seconds_ago: pd.Series, values: pd.Series) -> float:
    if len(values) < 2:
        return 0.0
    x = (-seconds_ago).to_numpy(dtype=float)  # increasing with time
    y = values.to_numpy(dtype=float)
    if x.max() == x.min():
        return 0.0
    return float(np.polyfit(x, y, 1)[0])


def compute_features(
    metric_history: pd.DataFrame,
    event_history: pd.DataFrame,
    as_of: pd.Timestamp,
) -> dict[str, float] | None:
    """metric_history: columns [ts, node_id, interface, utilization_pct, latency_ms,
    jitter_ms, packet_loss_pct] for a single node_id+interface, any row order.
    event_history: columns [ts, node_id, severity] for that node_id.
    Rows with ts after `as_of` are ignored. Returns None if there's less than
    WINDOW_SECONDS[0] seconds of history at-or-before `as_of`."""
    hist = metric_history[metric_history["ts"] <= as_of].copy()
    if hist.empty:
        return None
    hist["seconds_ago"] = (as_of - hist["ts"]).dt.total_seconds()
    if hist["seconds_ago"].max() < WINDOW_SECONDS[0]:
        return None

    features: dict[str, float] = {}
    for window in WINDOW_SECONDS:
        windowed = hist[hist["seconds_ago"] <= window]
        for metric in METRIC_COLS:
            values = windowed[metric]
            prefix = f"{metric}_{window}s_"
            if values.empty:
                features[prefix + "mean"] = 0.0
                features[prefix + "std"] = 0.0
                features[prefix + "min"] = 0.0
                features[prefix + "max"] = 0.0
                features[prefix + "slope"] = 0.0
                continue
            features[prefix + "mean"] = float(values.mean())
            features[prefix + "std"] = float(values.std()) if len(values) > 1 else 0.0
            features[prefix + "min"] = float(values.min())
            features[prefix + "max"] = float(values.max())
            features[prefix + "slope"] = _slope(windowed["seconds_ago"], values)

    largest = WINDOW_SECONDS[-1]
    ev = event_history[event_history["ts"] <= as_of].copy()
    if not ev.empty:
        ev["seconds_ago"] = (as_of - ev["ts"]).dt.total_seconds()
        ev = ev[ev["seconds_ago"] <= largest]
    if ev.empty:
        features["event_count_120s"] = 0.0
        features["event_severity_max_120s"] = 0.0
    else:
        features["event_count_120s"] = float(len(ev))
        features["event_severity_max_120s"] = float(ev["severity"].map(SEVERITY_RANK).max())

    return features
