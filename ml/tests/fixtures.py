"""Shared test fixtures for ml/tests/. Not collected by pytest as a test module
(no test_ prefix) — import synthetic_training_set() directly where needed."""
from __future__ import annotations

import pandas as pd

from ml.features import feature_names


def synthetic_training_set() -> pd.DataFrame:
    """2 nodes x 2 runs x (baseline, ramp) phases x 5 rows = 40 rows, with 4
    distinct contiguous ramp blocks — enough distinct fault-run groups for
    GroupShuffleSplit in ml/train.py to produce a safe train/test split."""
    features = feature_names()
    rows = []
    ts = pd.Timestamp("2026-07-13T00:00:00Z")
    for node_id in ("pe-east", "pe-west"):
        for _run in range(2):
            for is_precursor in (0, 1):
                for i in range(5):
                    row = {name: (float(i) if is_precursor else 0.0) for name in features}
                    row["node_id"] = node_id
                    row["interface"] = "eth0"
                    row["ts"] = ts
                    row["is_precursor"] = is_precursor
                    row["seconds_to_impact"] = float(60 - i * 10) if is_precursor else None
                    rows.append(row)
                    ts += pd.Timedelta(seconds=5)
    return pd.DataFrame(rows)
