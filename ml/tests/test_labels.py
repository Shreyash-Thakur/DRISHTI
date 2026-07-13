import pandas as pd

from ml.labels import is_in_ramp_window, progress, seconds_to_impact


def test_seconds_to_impact_at_ramp_start_equals_ramp_seconds():
    started_at = pd.Timestamp("2026-07-13T00:00:00Z")
    assert seconds_to_impact(started_at, 600, started_at) == 600


def test_seconds_to_impact_at_impact_is_zero():
    started_at = pd.Timestamp("2026-07-13T00:00:00Z")
    impact = started_at + pd.Timedelta(seconds=600)
    assert seconds_to_impact(started_at, 600, impact) == 0


def test_progress_clamped_to_zero_before_start():
    started_at = pd.Timestamp("2026-07-13T00:00:00Z")
    before = started_at - pd.Timedelta(seconds=10)
    assert progress(started_at, 600, before) == 0.0


def test_progress_clamped_to_one_after_ramp():
    started_at = pd.Timestamp("2026-07-13T00:00:00Z")
    after = started_at + pd.Timedelta(seconds=1000)
    assert progress(started_at, 600, after) == 1.0


def test_progress_halfway():
    started_at = pd.Timestamp("2026-07-13T00:00:00Z")
    halfway = started_at + pd.Timedelta(seconds=300)
    assert progress(started_at, 600, halfway) == 0.5


def test_is_in_ramp_window_boundaries():
    started_at = pd.Timestamp("2026-07-13T00:00:00Z")
    assert is_in_ramp_window(started_at, 600, started_at) is True
    assert is_in_ramp_window(started_at, 600, started_at + pd.Timedelta(seconds=600)) is True
    assert is_in_ramp_window(started_at, 600, started_at + pd.Timedelta(seconds=601)) is False
    assert is_in_ramp_window(started_at, 600, started_at - pd.Timedelta(seconds=1)) is False
