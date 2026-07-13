"""Ground-truth label computation for the time-to-impact regressor. The
simulator's fault ramp is deterministic (simulator/sim/faults.py ActiveFault),
so every ramp-phase telemetry sample gets an exact label — see
docs/superpowers/specs/2026-07-13-phase2-predictive-fault-engine-design.md."""
from __future__ import annotations

import pandas as pd


def progress(started_at: pd.Timestamp, ramp_seconds: float, sample_ts: pd.Timestamp) -> float:
    elapsed = (sample_ts - started_at).total_seconds()
    return max(0.0, min(1.0, elapsed / ramp_seconds))


def is_in_ramp_window(started_at: pd.Timestamp, ramp_seconds: float, sample_ts: pd.Timestamp) -> bool:
    """True while progress is in [0, 1] — sample_ts between started_at and impact, inclusive."""
    if sample_ts < started_at:
        return False
    return sample_ts <= started_at + pd.Timedelta(seconds=ramp_seconds)


def seconds_to_impact(started_at: pd.Timestamp, ramp_seconds: float, sample_ts: pd.Timestamp) -> float:
    """seconds_to_impact = fault.started_at + ramp_seconds - sample.ts. Only meaningful
    when is_in_ramp_window(started_at, ramp_seconds, sample_ts) is True."""
    impact_at = started_at + pd.Timedelta(seconds=ramp_seconds)
    return (impact_at - sample_ts).total_seconds()
