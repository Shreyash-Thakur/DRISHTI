# Phase 2 Predictive Fault Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up `ml/` as a standalone FastAPI service (`:8200`) that forecasts time-to-impact for in-progress fault precursors, fully offline, per `docs/superpowers/specs/2026-07-13-phase2-predictive-fault-engine-design.md`.

**Architecture:** A data-gen harness drives the simulator's fault API to build a labelled `training.parquet`; `train.py` fits a LightGBM precursor classifier (binary) and a LightGBM time-to-impact regressor (ramp-phase rows only) from shared rolling-window features; a live FastAPI service loads both models, consumes backend's `/ws/live`, maintains in-memory per-node/interface buffers, and serves `GET /predictions`, `GET /predictions/{node_id}`, `WS /ws/predictions`.

**Tech Stack:** Python, FastAPI/uvicorn/pydantic-settings (matching backend/simulator), pandas + pyarrow, lightgbm, scikit-learn (splitting/metrics only), httpx (fault injection + TestClient), websockets (WS client to backend), pytest (new to this repo — no existing test suite to match).

## Global Constraints

- Zero outbound network calls — the ml service only talks to `localhost` backend/simulator. No telemetry, no package downloads at runtime.
- New service listens on `:8200`, following the `backend` (`:8000`) / `simulator` (`:8100`) port convention already in `docker-compose.yml`.
- Settings via `pydantic-settings` with env prefix `ML_`, matching `backend`'s `DRISHTI_` and `simulator`'s `SIM_` prefix pattern (`backend/app/config.py`, `simulator/sim/config.py`).
- No ORM — raw `sqlite3`/pandas for offline reads of `data/drishti.db`, consistent with `backend/app/repository/db.py`.
- Dependency pins use the same loose `>=` minimum-version style as `backend/requirements.txt` / `simulator/requirements.txt`.
- This repo has no existing test suite or pytest config — this plan establishes `ml/tests/` + `ml/pytest.ini` fresh; don't try to match a prior convention that doesn't exist.
- Model artifacts (`ml/models/*`) and generated datasets (`ml/dataset/training.parquet`, `ml/dataset/manifest.json`) are git-ignored — they're regenerable, not source.
- Fault scenarios are exactly 3: `congestion_ramp`, `bgp_flap_precursor`, `link_degradation` (`simulator/sim/faults.py:18-34`) — don't invent others.
- Label math is exact, not heuristic: `seconds_to_impact = fault.started_at + ramp_seconds - sample.ts`, valid only while `0 <= progress <= 1`.

---

## Task 1: `ml/` package scaffolding, config, dependencies

**Files:**
- Create: `ml/requirements.txt`
- Create: `ml/config.py`
- Create: `ml/pytest.ini`
- Create: `ml/tests/__init__.py`
- Create: `ml/tests/test_config.py`
- Create: `ml/dataset/__init__.py`
- Create: `ml/service/__init__.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `ml.config.Settings` (fields: `port`, `backend_http_url`, `backend_ws_url`, `simulator_url`, `db_path`, `topology_path`, `dataset_dir`, `model_dir`, `precursor_threshold`) and `ml.config.get_settings() -> Settings` (lru-cached), used by every later task.

- [ ] **Step 1: Create `ml/requirements.txt`**

```
fastapi>=0.110
uvicorn[standard]>=0.29
pydantic>=2.6
pydantic-settings>=2.2
httpx>=0.27
websockets>=12.0
pandas>=2.2
pyarrow>=15.0
numpy>=1.26
scikit-learn>=1.4
lightgbm>=4.3
pytest>=8.0
```

- [ ] **Step 2: Create `ml/config.py`**

```python
"""Settings for the ml package — dataset generation, training, and the live
service all read the same Settings so paths/URLs stay in sync."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ML_")

    port: int = 8200
    backend_http_url: str = "http://localhost:8000"
    backend_ws_url: str = "ws://localhost:8000/ws/live"
    simulator_url: str = "http://localhost:8100"
    db_path: Path = Path("data/drishti.db")
    topology_path: Path = Path("data/topology.json")
    dataset_dir: Path = Path("ml/dataset")
    model_dir: Path = Path("ml/models")
    precursor_threshold: float = 0.5


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 3: Create `ml/pytest.ini`**

```ini
[pytest]
testpaths = tests
```

- [ ] **Step 4: Create empty `ml/tests/__init__.py`, `ml/dataset/__init__.py`, `ml/service/__init__.py`**

All three are empty files (matching `ml/__init__.py`'s existing empty style).

- [ ] **Step 5: Write the failing test — `ml/tests/test_config.py`**

```python
from ml.config import Settings, get_settings


def test_default_settings_match_port_convention():
    settings = Settings()
    assert settings.port == 8200
    assert settings.backend_ws_url == "ws://localhost:8000/ws/live"
    assert settings.precursor_threshold == 0.5


def test_get_settings_is_cached():
    assert get_settings() is get_settings()
```

- [ ] **Step 6: Run test to verify it fails (module doesn't exist yet)**

Run (from repo root): `cd ml && python -m pytest tests/test_config.py -v`
Expected: FAIL / ModuleNotFoundError before step 2's file exists — since step 2 already created it above, instead run this immediately after step 2 to confirm PASS. If running strictly in order, skip straight to step 7.

- [ ] **Step 7: Run test to verify it passes**

Run: `cd ml && python -m pip install -r requirements.txt && python -m pytest tests/test_config.py -v`
Expected: `2 passed`

- [ ] **Step 8: Update `.gitignore`**

Add after the existing "Data artifacts" block:

```
# ml artifacts (regenerable)
ml/models/
ml/dataset/*.parquet
ml/dataset/manifest.json
```

- [ ] **Step 9: Commit**

```bash
git add ml/requirements.txt ml/config.py ml/pytest.ini ml/tests/__init__.py ml/tests/test_config.py ml/dataset/__init__.py ml/service/__init__.py .gitignore
git commit -m "ml: scaffold package, settings, pytest config"
```

---

## Task 2: Shared rolling-window feature engineering (`ml/features.py`)

**Files:**
- Create: `ml/features.py`
- Test: `ml/tests/test_features.py`

**Interfaces:**
- Consumes: nothing from earlier tasks except `Settings` is not needed here (pure functions).
- Produces: `ml.features.METRIC_COLS`, `ml.features.WINDOW_SECONDS = (30, 60, 120)`, `ml.features.feature_names() -> list[str]`, `ml.features.compute_features(metric_history: pd.DataFrame, event_history: pd.DataFrame, as_of: pd.Timestamp) -> dict[str, float] | None`. `metric_history` columns: `ts, node_id, interface, utilization_pct, latency_ms, jitter_ms, packet_loss_pct`. `event_history` columns: `ts, node_id, severity`. Returns `None` when there's less than `WINDOW_SECONDS[0]` (30s) of history at-or-before `as_of`. Used verbatim by Task 4 (dataset harness), Task 5 (train.py via `feature_names()`), and Task 6 (live predictor).

- [ ] **Step 1: Write the failing tests — `ml/tests/test_features.py`**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ml && python -m pytest tests/test_features.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ml.features'` (or ImportError)

- [ ] **Step 3: Implement `ml/features.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ml && python -m pytest tests/test_features.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add ml/features.py ml/tests/test_features.py
git commit -m "ml: add shared rolling-window feature engineering"
```

---

## Task 3: Ground-truth label computation (`ml/labels.py`)

**Files:**
- Create: `ml/labels.py`
- Test: `ml/tests/test_labels.py`

**Interfaces:**
- Produces: `ml.labels.progress(started_at, ramp_seconds, sample_ts) -> float`, `ml.labels.is_in_ramp_window(started_at, ramp_seconds, sample_ts) -> bool`, `ml.labels.seconds_to_impact(started_at, ramp_seconds, sample_ts) -> float`. All take `pd.Timestamp` for `started_at`/`sample_ts` and `float` (seconds) for `ramp_seconds`. Used by Task 4's `build_training_set`.

- [ ] **Step 1: Write the failing tests — `ml/tests/test_labels.py`**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ml && python -m pytest tests/test_labels.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ml.labels'`

- [ ] **Step 3: Implement `ml/labels.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ml && python -m pytest tests/test_labels.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add ml/labels.py ml/tests/test_labels.py
git commit -m "ml: add ground-truth time-to-impact label math"
```

---

## Task 4: Dataset harness — fault injection + training set builder (`ml/dataset/generate.py`)

**Files:**
- Create: `ml/dataset/generate.py`
- Test: `ml/tests/test_generate.py`

**Interfaces:**
- Consumes: `ml.config.get_settings()` (Task 1), `ml.features.compute_features` (Task 2), `ml.labels.is_in_ramp_window`/`seconds_to_impact` (Task 3).
- Produces: `ml.dataset.generate.load_topology(path) -> dict`, `node_interfaces(topology: dict) -> dict[str, list[str]]`, `inject_fault(client, scenario, node_id, interface, ramp_seconds, hold_seconds) -> dict`, `clear_fault(client, fault_id) -> None`, `run_harness(simulator_url, topology_path, manifest_path) -> list[dict]` (manifest entries: `{fault_id, scenario, node_id, interface, started_at (ISO str), ramp_seconds, hold_seconds}`), `load_metrics_and_events(db_path) -> tuple[pd.DataFrame, pd.DataFrame]`, `build_training_set(manifest: list[dict], metrics: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame` (columns: all of `feature_names()`, plus `node_id, interface, ts, is_precursor (0/1), seconds_to_impact (float | None)`). Task 5's `train.py` reads the parquet this produces.

- [ ] **Step 1: Write the failing tests — `ml/tests/test_generate.py`**

These test the pure/offline pieces (`node_interfaces`, `build_training_set`) — `run_harness`/`inject_fault`/`clear_fault` need a live simulator and are covered by manual verification in Task 7, not unit tests.

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ml && python -m pytest tests/test_generate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ml.dataset.generate'`

- [ ] **Step 3: Implement `ml/dataset/generate.py`**

```python
"""Data-gen harness: drives fault injection against the simulator (:8100), then
builds a labelled training set from data/drishti.db. Run manually — assumes the
simulator and backend are already running (see ml/README.md). Not a service."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import httpx
import pandas as pd

from ml.config import get_settings
from ml.features import compute_features
from ml.labels import is_in_ramp_window, seconds_to_impact

SCENARIOS = ["congestion_ramp", "bgp_flap_precursor", "link_degradation"]
RAMP_SECONDS = 120
HOLD_SECONDS = 30
BASELINE_GAP_SECONDS = 60


def load_topology(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def node_interfaces(topology: dict) -> dict[str, list[str]]:
    """node_id -> interface names, derived from each link's a/b endpoints."""
    result: dict[str, list[str]] = {}
    for link in topology["links"]:
        for end in (link["a"], link["b"]):
            result.setdefault(end["node"], []).append(end["interface"])
    return result


def inject_fault(
    client: httpx.Client,
    scenario: str,
    node_id: str,
    interface: str,
    ramp_seconds: int,
    hold_seconds: int,
) -> dict:
    """POSTs /faults with retries; raises after 3 failed attempts."""
    body = {
        "scenario": scenario,
        "node_id": node_id,
        "interface": interface,
        "params": {"ramp_seconds": ramp_seconds, "hold_seconds": hold_seconds},
    }
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = client.post("/faults", json=body, timeout=10.0)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            last_error = exc
            time.sleep(2**attempt)
    raise RuntimeError(f"failed to inject {scenario} on {node_id}/{interface}") from last_error


def clear_fault(client: httpx.Client, fault_id: str) -> None:
    """Best-effort clear — a fault that fails to clear self-expires via hold_seconds,
    so this must never raise (it always runs, success or failure, per-run)."""
    try:
        client.delete(f"/faults/{fault_id}", timeout=10.0)
    except httpx.HTTPError:
        pass


def run_harness(simulator_url: str, topology_path: Path, manifest_path: Path) -> list[dict]:
    topology = load_topology(topology_path)
    targets = node_interfaces(topology)
    manifest: list[dict] = []
    with httpx.Client(base_url=simulator_url) as client:
        for scenario in SCENARIOS:
            for node_id, interfaces in targets.items():
                interface = interfaces[0]
                fault = None
                try:
                    fault = inject_fault(client, scenario, node_id, interface, RAMP_SECONDS, HOLD_SECONDS)
                    manifest.append({
                        "fault_id": fault["fault_id"],
                        "scenario": scenario,
                        "node_id": node_id,
                        "interface": interface,
                        "started_at": fault["started_at"],
                        "ramp_seconds": RAMP_SECONDS,
                        "hold_seconds": HOLD_SECONDS,
                    })
                    time.sleep(RAMP_SECONDS + HOLD_SECONDS)
                finally:
                    if fault is not None:
                        clear_fault(client, fault["fault_id"])
                time.sleep(BASELINE_GAP_SECONDS)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest


def load_metrics_and_events(db_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    conn = sqlite3.connect(db_path)
    try:
        metrics = pd.read_sql_query(
            "SELECT ts, node_id, interface, utilization_pct, latency_ms, jitter_ms, "
            "packet_loss_pct FROM interface_metrics ORDER BY ts",
            conn,
        )
        events = pd.read_sql_query(
            "SELECT ts, node_id, severity FROM events ORDER BY ts", conn,
        )
    finally:
        conn.close()
    metrics["ts"] = pd.to_datetime(metrics["ts"], utc=True)
    events["ts"] = pd.to_datetime(events["ts"], utc=True)
    return metrics, events


def build_training_set(manifest: list[dict], metrics: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    manifest_by_key: dict[tuple[str, str], list[dict]] = {}
    for entry in manifest:
        manifest_by_key.setdefault((entry["node_id"], entry["interface"]), []).append(entry)

    rows: list[dict] = []
    for (node_id, interface), group in metrics.groupby(["node_id", "interface"]):
        group = group.sort_values("ts").reset_index(drop=True)
        node_events = events[events["node_id"] == node_id]
        entries = manifest_by_key.get((node_id, interface), [])
        for i in range(len(group)):
            as_of = group.loc[i, "ts"]
            feats = compute_features(group.iloc[: i + 1], node_events, as_of)
            if feats is None:
                continue
            match = next(
                (e for e in entries if is_in_ramp_window(
                    pd.Timestamp(e["started_at"]), e["ramp_seconds"], as_of)),
                None,
            )
            row = dict(feats)
            row["node_id"] = node_id
            row["interface"] = interface
            row["ts"] = as_of
            if match is None:
                row["is_precursor"] = 0
                row["seconds_to_impact"] = None
            else:
                row["is_precursor"] = 1
                row["seconds_to_impact"] = seconds_to_impact(
                    pd.Timestamp(match["started_at"]), match["ramp_seconds"], as_of,
                )
            rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    settings = get_settings()
    manifest = run_harness(
        settings.simulator_url, settings.topology_path, settings.dataset_dir / "manifest.json",
    )
    metrics, events = load_metrics_and_events(settings.db_path)
    training_set = build_training_set(manifest, metrics, events)
    settings.dataset_dir.mkdir(parents=True, exist_ok=True)
    out_path = settings.dataset_dir / "training.parquet"
    training_set.to_parquet(out_path, index=False)
    print(f"wrote {len(training_set)} rows to {out_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ml && python -m pytest tests/test_generate.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add ml/dataset/generate.py ml/tests/test_generate.py
git commit -m "ml: add fault-injection data-gen harness and training-set builder"
```

---

## Task 5: Training pipeline (`ml/train.py`)

**Files:**
- Create: `ml/train.py`
- Create: `ml/tests/fixtures.py`
- Test: `ml/tests/test_train.py`

**Interfaces:**
- Consumes: `ml.features.feature_names()` (Task 2), `ml.config.get_settings()` (Task 1).
- Produces: `ml.train.load_training_set(path) -> pd.DataFrame`, `ml.train.train_classifier(df) -> tuple[lgb.LGBMClassifier, float]` (returns fitted model + held-out AUC), `ml.train.train_regressor(df) -> tuple[lgb.LGBMRegressor, float]` (ramp-only rows, returns fitted model + held-out MAE seconds), `ml.train.save_models(classifier, regressor, model_dir) -> None` (writes `classifier.txt`, `regressor.txt`, `features.json` via LightGBM's raw `Booster.save_model`, not pickle — avoids version-lock issues offline). `ml/tests/fixtures.py` exposes `synthetic_training_set() -> pd.DataFrame`, reused by Task 6's predictor tests.

- [ ] **Step 1: Write `ml/tests/fixtures.py` (shared test fixture, not itself a test file)**

```python
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
```

- [ ] **Step 2: Write the failing tests — `ml/tests/test_train.py`**

```python
import lightgbm as lgb

from ml.features import feature_names
from ml.tests.fixtures import synthetic_training_set
from ml.train import save_models, train_classifier, train_regressor


def test_train_classifier_and_regressor_produce_usable_models(tmp_path):
    df = synthetic_training_set()

    classifier, auc = train_classifier(df)
    regressor, mae = train_regressor(df)
    assert 0.0 <= auc <= 1.0
    assert mae >= 0.0

    save_models(classifier, regressor, tmp_path)
    assert (tmp_path / "classifier.txt").exists()
    assert (tmp_path / "regressor.txt").exists()
    assert (tmp_path / "features.json").exists()

    loaded = lgb.Booster(model_file=str(tmp_path / "classifier.txt"))
    prediction = loaded.predict(df[feature_names()].iloc[:1])
    assert len(prediction) == 1


def test_load_training_set_raises_on_empty_parquet(tmp_path):
    import pandas as pd

    from ml.train import load_training_set

    empty_path = tmp_path / "empty.parquet"
    pd.DataFrame({"a": []}).to_parquet(empty_path)
    try:
        load_training_set(empty_path)
        assert False, "expected ValueError"
    except ValueError:
        pass
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd ml && python -m pytest tests/test_train.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ml.train'`

- [ ] **Step 4: Implement `ml/train.py`**

```python
"""Trains the precursor classifier and time-to-impact regressor from
ml/dataset/training.parquet (see ml/dataset/generate.py), saves both plus the
feature list to ml/models/. Run manually after generate.py: `python -m ml.train`."""
from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import pandas as pd
from sklearn.metrics import mean_absolute_error, roc_auc_score
from sklearn.model_selection import GroupShuffleSplit

from ml.config import get_settings
from ml.features import feature_names


def load_training_set(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if df.empty:
        raise ValueError(f"training set at {path} is empty — run ml/dataset/generate.py first")
    return df


def _fault_run_group(df: pd.DataFrame) -> pd.Series:
    """Leave-one-scenario-instance-out grouping: every contiguous run of the same
    node_id+interface+is_precursor value is one group, so a train/test split never
    puts samples from the same fault run (or the same baseline stretch) on both
    sides — that would leak and inflate held-out metrics."""
    key = df["node_id"] + "|" + df["interface"]
    key_changed = key != key.shift()
    precursor_changed = df["is_precursor"].diff().fillna(0) != 0
    return (key_changed | precursor_changed).cumsum()


def train_classifier(df: pd.DataFrame) -> tuple[lgb.LGBMClassifier, float]:
    features = feature_names()
    groups = _fault_run_group(df)
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=0)
    train_idx, test_idx = next(splitter.split(df, groups=groups))
    model = lgb.LGBMClassifier(n_estimators=200, random_state=0)
    model.fit(df.iloc[train_idx][features], df.iloc[train_idx]["is_precursor"])
    predictions = model.predict_proba(df.iloc[test_idx][features])[:, 1]
    auc = roc_auc_score(df.iloc[test_idx]["is_precursor"], predictions)
    return model, float(auc)


def train_regressor(df: pd.DataFrame) -> tuple[lgb.LGBMRegressor, float]:
    features = feature_names()
    ramp_only = df[df["is_precursor"] == 1].reset_index(drop=True)
    groups = _fault_run_group(ramp_only)
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=0)
    train_idx, test_idx = next(splitter.split(ramp_only, groups=groups))
    model = lgb.LGBMRegressor(n_estimators=200, random_state=0)
    model.fit(ramp_only.iloc[train_idx][features], ramp_only.iloc[train_idx]["seconds_to_impact"])
    predictions = model.predict(ramp_only.iloc[test_idx][features])
    mae = mean_absolute_error(ramp_only.iloc[test_idx]["seconds_to_impact"], predictions)
    return model, float(mae)


def save_models(classifier: lgb.LGBMClassifier, regressor: lgb.LGBMRegressor, model_dir: Path) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    classifier.booster_.save_model(str(model_dir / "classifier.txt"))
    regressor.booster_.save_model(str(model_dir / "regressor.txt"))
    (model_dir / "features.json").write_text(json.dumps(feature_names()))


def main() -> None:
    settings = get_settings()
    df = load_training_set(settings.dataset_dir / "training.parquet")
    classifier, auc = train_classifier(df)
    regressor, mae = train_regressor(df)
    print(f"classifier held-out AUC: {auc:.3f}")
    print(f"regressor held-out MAE: {mae:.1f}s")
    save_models(classifier, regressor, settings.model_dir)
    print(f"saved models to {settings.model_dir}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd ml && python -m pytest tests/test_train.py -v`
Expected: `2 passed`

- [ ] **Step 6: Commit**

```bash
git add ml/train.py ml/tests/fixtures.py ml/tests/test_train.py
git commit -m "ml: add classifier/regressor training pipeline"
```

---

## Task 6: Live service — buffers, predictor, WS client, routes (`ml/service/`)

**Files:**
- Create: `ml/service/state.py`
- Create: `ml/service/predictor.py`
- Create: `ml/service/broadcaster.py`
- Create: `ml/service/ws_client.py`
- Create: `ml/service/routes.py`
- Create: `ml/service/app.py`
- Create: `ml/main.py`
- Test: `ml/tests/test_state.py`
- Test: `ml/tests/test_predictor.py`
- Test: `ml/tests/test_routes.py`
- Test: `ml/tests/test_ws_client.py`

**Interfaces:**
- Consumes: `ml.features.WINDOW_SECONDS`/`compute_features` (Task 2), `ml.config.get_settings()` (Task 1), `ml.tests.fixtures.synthetic_training_set` + `ml.train.{train_classifier,train_regressor,save_models}` (Task 5) for predictor tests.
- Produces:
  - `ml.service.state.LiveState` — methods `add_metric(row: dict)`, `add_event(row: dict)`, `metric_frame(node_id, interface) -> pd.DataFrame`, `event_frame(node_id) -> pd.DataFrame`, `set_prediction(node_id, interface, prediction: dict)`, `get_prediction(node_id, interface) -> dict | None`, `predictions_for_node(node_id) -> list[dict]`, `all_predictions() -> list[dict]`. `row["ts"]` must be a `pandas.Timestamp` or `datetime`.
  - `ml.service.predictor.Predictor(model_dir: Path, threshold: float)` — raises `FileNotFoundError` if any of `classifier.txt`/`regressor.txt`/`features.json` is missing, raises `ValueError` if the saved feature list doesn't match `feature_names()`. Method `predict(node_id, interface, metric_history, event_history, as_of) -> dict` returning `{node_id, interface, precursor_probability, estimated_seconds_to_impact}` (both `None` when history is insufficient; `estimated_seconds_to_impact` is `None` when `precursor_probability < threshold`).
  - `ml.service.broadcaster.Broadcaster` — `register(ws)`, `unregister(ws)`, `publish(prediction: dict)` (async).
  - `ml.service.ws_client.run_ws_client(ws_url, state, predictor, broadcast)` (async, runs forever, reconnects with exponential backoff) and `_handle_message(raw: str, state, predictor, broadcast)` (async, parses one `/ws/live` message).
  - `ml.service.routes.router` — FastAPI `APIRouter` with `GET /health`, `GET /predictions`, `GET /predictions/{node_id}`, `WS /ws/predictions`, reading `request.app.state.live_state` / `ws.app.state.broadcaster`.
  - `ml.service.app.create_app() -> FastAPI` and module-level `app`.
  - `ml.main.main()` — `uvicorn.run("ml.service.app:app", ...)`.

- [ ] **Step 1: Write the failing tests — `ml/tests/test_state.py`**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ml && python -m pytest tests/test_state.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'ml.service.state'`

- [ ] **Step 3: Implement `ml/service/state.py`**

```python
"""In-memory rolling buffers + latest predictions, shared by the WS client
(writer, ml/service/ws_client.py) and the HTTP/WS routes (readers,
ml/service/routes.py). Single instance per process, both run on the same
asyncio event loop with no `await` mid-mutation, so no locking is needed."""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta

import pandas as pd

from ml.features import WINDOW_SECONDS

RETENTION = timedelta(seconds=WINDOW_SECONDS[-1] + 10)

_METRIC_COLUMNS = ["ts", "node_id", "interface", "utilization_pct", "latency_ms", "jitter_ms", "packet_loss_pct"]
_EVENT_COLUMNS = ["ts", "node_id", "severity"]


class LiveState:
    def __init__(self) -> None:
        self.metric_rows: dict[tuple[str, str], deque] = defaultdict(deque)
        self.event_rows: dict[str, deque] = defaultdict(deque)
        self.predictions: dict[tuple[str, str], dict] = {}

    def add_metric(self, row: dict) -> None:
        key = (row["node_id"], row["interface"])
        buf = self.metric_rows[key]
        buf.append(row)
        self._prune(buf, row["ts"])

    def add_event(self, row: dict) -> None:
        buf = self.event_rows[row["node_id"]]
        buf.append(row)
        self._prune(buf, row["ts"])

    @staticmethod
    def _prune(buf: deque, latest_ts: datetime) -> None:
        cutoff = latest_ts - RETENTION
        while buf and buf[0]["ts"] < cutoff:
            buf.popleft()

    def metric_frame(self, node_id: str, interface: str) -> pd.DataFrame:
        rows = list(self.metric_rows.get((node_id, interface), ()))
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=_METRIC_COLUMNS)

    def event_frame(self, node_id: str) -> pd.DataFrame:
        rows = list(self.event_rows.get(node_id, ()))
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=_EVENT_COLUMNS)

    def set_prediction(self, node_id: str, interface: str, prediction: dict) -> None:
        self.predictions[(node_id, interface)] = prediction

    def get_prediction(self, node_id: str, interface: str) -> dict | None:
        return self.predictions.get((node_id, interface))

    def predictions_for_node(self, node_id: str) -> list[dict]:
        return [p for (n, _i), p in self.predictions.items() if n == node_id]

    def all_predictions(self) -> list[dict]:
        return list(self.predictions.values())
```

- [ ] **Step 4: Run to verify pass**

Run: `cd ml && python -m pytest tests/test_state.py -v`
Expected: `3 passed`

- [ ] **Step 5: Write the failing tests — `ml/tests/test_predictor.py`**

```python
import pandas as pd
import pytest

from ml.service.predictor import Predictor
from ml.tests.fixtures import synthetic_training_set
from ml.train import save_models, train_classifier, train_regressor


def _train_tiny_models(tmp_path):
    df = synthetic_training_set()
    classifier, _ = train_classifier(df)
    regressor, _ = train_regressor(df)
    save_models(classifier, regressor, tmp_path)
    return tmp_path


def test_predictor_raises_if_models_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        Predictor(tmp_path, threshold=0.5)


def test_predictor_returns_none_fields_for_insufficient_history(tmp_path):
    model_dir = _train_tiny_models(tmp_path)
    predictor = Predictor(model_dir, threshold=0.5)
    as_of = pd.Timestamp("2026-07-13T00:00:05Z")
    metric_history = pd.DataFrame({
        "ts": [as_of], "node_id": ["pe-east"], "interface": ["eth0"],
        "utilization_pct": [10.0], "latency_ms": [5.0], "jitter_ms": [1.0], "packet_loss_pct": [0.0],
    })
    event_history = pd.DataFrame(columns=["ts", "node_id", "severity"])
    result = predictor.predict("pe-east", "eth0", metric_history, event_history, as_of)
    assert result == {
        "node_id": "pe-east", "interface": "eth0",
        "precursor_probability": None, "estimated_seconds_to_impact": None,
    }


def test_predictor_returns_numeric_fields_with_enough_history(tmp_path):
    model_dir = _train_tiny_models(tmp_path)
    predictor = Predictor(model_dir, threshold=0.0)  # threshold 0 forces the regressor to always run
    as_of = pd.Timestamp("2026-07-13T00:02:00Z")
    ts = pd.date_range(end=as_of, periods=25, freq="5s")
    metric_history = pd.DataFrame({
        "ts": ts, "node_id": ["pe-east"] * 25, "interface": ["eth0"] * 25,
        "utilization_pct": [10.0 + i for i in range(25)],
        "latency_ms": [5.0] * 25, "jitter_ms": [1.0] * 25, "packet_loss_pct": [0.0] * 25,
    })
    event_history = pd.DataFrame(columns=["ts", "node_id", "severity"])
    result = predictor.predict("pe-east", "eth0", metric_history, event_history, as_of)
    assert isinstance(result["precursor_probability"], float)
    assert isinstance(result["estimated_seconds_to_impact"], float)
```

- [ ] **Step 6: Run to verify failure**

Run: `cd ml && python -m pytest tests/test_predictor.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'ml.service.predictor'`

- [ ] **Step 7: Implement `ml/service/predictor.py`**

```python
"""Loads trained models (Task 5's ml/train.py output) and runs
classifier -> (if above threshold) -> regressor inference for one node/interface."""
from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import pandas as pd

from ml.features import compute_features, feature_names


class Predictor:
    def __init__(self, model_dir: Path, threshold: float) -> None:
        classifier_path = Path(model_dir) / "classifier.txt"
        regressor_path = Path(model_dir) / "regressor.txt"
        features_path = Path(model_dir) / "features.json"
        for path in (classifier_path, regressor_path, features_path):
            if not path.exists():
                raise FileNotFoundError(
                    f"missing model artifact {path} — run `python -m ml.train` before starting the service"
                )
        saved_features = json.loads(features_path.read_text())
        expected = feature_names()
        if saved_features != expected:
            raise ValueError(
                "ml/models/features.json does not match ml.features.feature_names() — "
                "retrain with `python -m ml.train` after changing ml/features.py"
            )
        self.classifier = lgb.Booster(model_file=str(classifier_path))
        self.regressor = lgb.Booster(model_file=str(regressor_path))
        self.features = saved_features
        self.threshold = threshold

    def predict(
        self,
        node_id: str,
        interface: str,
        metric_history: pd.DataFrame,
        event_history: pd.DataFrame,
        as_of: pd.Timestamp,
    ) -> dict:
        feats = compute_features(metric_history, event_history, as_of)
        if feats is None:
            return {
                "node_id": node_id, "interface": interface,
                "precursor_probability": None, "estimated_seconds_to_impact": None,
            }
        row = pd.DataFrame([feats])[self.features]
        probability = float(self.classifier.predict(row)[0])
        estimated_seconds_to_impact = None
        if probability >= self.threshold:
            estimated_seconds_to_impact = float(self.regressor.predict(row)[0])
        return {
            "node_id": node_id, "interface": interface,
            "precursor_probability": probability,
            "estimated_seconds_to_impact": estimated_seconds_to_impact,
        }
```

- [ ] **Step 8: Run to verify pass**

Run: `cd ml && python -m pytest tests/test_predictor.py -v`
Expected: `3 passed`

- [ ] **Step 9: Implement `ml/service/broadcaster.py`** (no dedicated unit test — exercised via `test_routes.py`'s WS test in Step 13; mirrors `backend/app/services/live_broadcaster.py`'s pattern)

```python
"""WS fan-out for /ws/predictions, mirroring backend/app/services/live_broadcaster.py."""
from __future__ import annotations

import asyncio

from fastapi import WebSocket


class Broadcaster:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def unregister(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def publish(self, prediction: dict) -> None:
        async with self._lock:
            clients = list(self._clients)
        dead = []
        for ws in clients:
            try:
                await ws.send_json({"type": "prediction", "prediction": prediction})
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)
```

- [ ] **Step 10: Implement `ml/service/routes.py`**

```python
"""HTTP + WS routes for the ml service. Reads shared state off `request.app.state`
/ `ws.app.state`, set up in ml/service/app.py's lifespan."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "drishti-ml"}


@router.get("/predictions")
async def all_predictions(request: Request) -> list[dict]:
    return request.app.state.live_state.all_predictions()


@router.get("/predictions/{node_id}")
async def node_predictions(node_id: str, request: Request) -> list[dict]:
    results = request.app.state.live_state.predictions_for_node(node_id)
    if not results:
        raise HTTPException(status_code=404, detail=f"no predictions for node_id={node_id!r} yet")
    return results


@router.websocket("/ws/predictions")
async def ws_predictions(ws: WebSocket) -> None:
    await ws.accept()
    broadcaster = ws.app.state.broadcaster
    await broadcaster.register(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.unregister(ws)
```

- [ ] **Step 11: Write the failing tests — `ml/tests/test_routes.py`**

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ml.service.broadcaster import Broadcaster
from ml.service.routes import router
from ml.service.state import LiveState


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.live_state = LiveState()
    app.state.broadcaster = Broadcaster()
    return TestClient(app)


def test_health():
    response = _client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "drishti-ml"}


def test_predictions_empty_list_when_nothing_seen_yet():
    response = _client().get("/predictions")
    assert response.status_code == 200
    assert response.json() == []


def test_node_predictions_404_for_unknown_node():
    response = _client().get("/predictions/does-not-exist")
    assert response.status_code == 404


def test_node_predictions_returns_seeded_prediction():
    client = _client()
    client.app.state.live_state.set_prediction("pe-east", "eth0", {
        "node_id": "pe-east", "interface": "eth0",
        "precursor_probability": 0.9, "estimated_seconds_to_impact": 42.0,
    })
    response = client.get("/predictions/pe-east")
    assert response.status_code == 200
    assert response.json()[0]["precursor_probability"] == 0.9


def test_ws_predictions_receives_broadcast_message():
    client = _client()
    with client.websocket_connect("/ws/predictions") as ws:
        import anyio

        async def _publish():
            await client.app.state.broadcaster.publish({"node_id": "pe-east", "interface": "eth0"})

        anyio.from_thread.run(_publish)
        message = ws.receive_json()
        assert message == {"type": "prediction", "prediction": {"node_id": "pe-east", "interface": "eth0"}}
```

- [ ] **Step 12: Run to verify failure**

Run: `cd ml && python -m pytest tests/test_routes.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'ml.service.routes'` (before Step 10) — since Step 10 already created it, running now should mostly pass except possibly the WS test if `anyio` threading nuances differ.

- [ ] **Step 13: Run to verify pass, fix the WS test if needed**

Run: `cd ml && python -m pytest tests/test_routes.py -v`
Expected: `5 passed`. If `test_ws_predictions_receives_broadcast_message` fails because `TestClient`'s websocket test session runs in its own thread and `anyio.from_thread.run` isn't reachable that way, replace the publish call with directly awaiting via `client.portal.call(client.app.state.broadcaster.publish, {...})` per `starlette.testclient`'s documented `portal` attribute — check `python -c "import starlette; print(starlette.__version__)"` and adjust to whatever pattern that version's `TestClient` supports; the goal (publish while a WS test client is connected, assert `ws.receive_json()` gets it) doesn't change.

- [ ] **Step 14: Write the failing tests — `ml/tests/test_ws_client.py`**

```python
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
```

Add an anyio backend fixture so `@pytest.mark.anyio` runs — create/extend `ml/tests/conftest.py`:

```python
import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
```

Add `anyio>=4.0` to `ml/requirements.txt` (pulled transitively by `httpx`/`starlette` already, but pin explicitly since it's imported directly in tests).

- [ ] **Step 15: Run to verify failure**

Run: `cd ml && python -m pytest tests/test_ws_client.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'ml.service.ws_client'`

- [ ] **Step 16: Implement `ml/service/ws_client.py`**

```python
"""WS client to backend's /ws/live: feeds LiveState and triggers inference on
every incoming batch, with exponential-backoff reconnect on disconnect."""
from __future__ import annotations

import asyncio
import json
import logging

import pandas as pd
import websockets

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 30


async def _handle_message(raw: str, state, predictor, broadcast) -> None:
    message = json.loads(raw)
    if message.get("type") != "telemetry":
        return
    batch = message["batch"]
    touched: set[tuple[str, str]] = set()
    for m in batch.get("interface_metrics", []):
        row = {**m, "ts": pd.Timestamp(m["ts"])}
        state.add_metric(row)
        touched.add((row["node_id"], row["interface"]))
    for e in batch.get("events", []):
        row = {**e, "ts": pd.Timestamp(e["ts"])}
        state.add_event(row)

    for node_id, interface in touched:
        metric_history = state.metric_frame(node_id, interface)
        event_history = state.event_frame(node_id)
        as_of = metric_history["ts"].max()
        prediction = predictor.predict(node_id, interface, metric_history, event_history, as_of)
        state.set_prediction(node_id, interface, prediction)
        await broadcast.publish(prediction)


async def run_ws_client(ws_url: str, state, predictor, broadcast) -> None:
    backoff = 1
    while True:
        try:
            async with websockets.connect(ws_url) as ws:
                logger.info("connected to %s", ws_url)
                backoff = 1
                async for raw in ws:
                    await _handle_message(raw, state, predictor, broadcast)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("ws connection to %s lost, retrying in %ss", ws_url, backoff, exc_info=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
```

- [ ] **Step 17: Run to verify pass**

Run: `cd ml && python -m pip install -r requirements.txt && python -m pytest tests/test_ws_client.py -v`
Expected: `2 passed`

- [ ] **Step 18: Implement `ml/service/app.py`**

```python
"""FastAPI app: loads models at startup (fatal if missing — see Predictor),
runs the backend WS client as a background task, exposes prediction routes."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ml.config import get_settings
from ml.service.broadcaster import Broadcaster
from ml.service.predictor import Predictor
from ml.service.routes import router
from ml.service.state import LiveState
from ml.service.ws_client import run_ws_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.live_state = LiveState()
    app.state.broadcaster = Broadcaster()
    app.state.predictor = Predictor(settings.model_dir, settings.precursor_threshold)
    task = asyncio.create_task(
        run_ws_client(settings.backend_ws_url, app.state.live_state, app.state.predictor, app.state.broadcaster)
    )
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def create_app() -> FastAPI:
    app = FastAPI(title="drishti-ml", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
```

- [ ] **Step 19: Implement `ml/main.py`**

```python
"""Entrypoint: `python -m ml.main` (or `uvicorn ml.service.app:app --port 8200`
from the ml/ directory)."""
from __future__ import annotations

import uvicorn

from ml.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run("ml.service.app:app", host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 20: Run the full ml test suite**

Run: `cd ml && python -m pytest -v`
Expected: all tests across `test_config.py`, `test_features.py`, `test_labels.py`, `test_generate.py`, `test_train.py`, `test_state.py`, `test_predictor.py`, `test_routes.py`, `test_ws_client.py` pass.

- [ ] **Step 21: Commit**

```bash
git add ml/service/state.py ml/service/predictor.py ml/service/broadcaster.py ml/service/ws_client.py ml/service/routes.py ml/service/app.py ml/main.py ml/tests/test_state.py ml/tests/test_predictor.py ml/tests/test_routes.py ml/tests/test_ws_client.py ml/tests/conftest.py ml/requirements.txt
git commit -m "ml: add live prediction service (buffers, predictor, WS client, routes)"
```

---

## Task 7: Docker Compose wiring + docs updates + manual verification

**Files:**
- Create: `ml/Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `ml/README.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1-6 (this task wires deployment + docs, no new code interfaces).

- [ ] **Step 1: Create `ml/Dockerfile`** (mirrors `backend/Dockerfile`/`simulator/Dockerfile` structure — read one first to match its exact base image/COPY pattern, e.g.:)

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8200

CMD ["python", "-m", "ml.main"]
```

(If `backend/Dockerfile`'s base image tag or healthcheck differs from the above guess, match `backend/Dockerfile` exactly instead — read it before writing this file, since the design spec requires the ml service to "mirror the existing simulator/backend pattern".)

- [ ] **Step 2: Modify `docker-compose.yml`** — add a third service after `simulator`, matching the existing two services' structure:

```yaml
  ml:
    build: ./ml
    container_name: drishti-ml
    ports:
      - "8200:8200"
    environment:
      ML_BACKEND_HTTP_URL: http://backend:8000
      ML_BACKEND_WS_URL: ws://backend:8000/ws/live
      ML_SIMULATOR_URL: http://simulator:8100
      ML_DB_PATH: /data/drishti.db
      ML_TOPOLOGY_PATH: /data/topology.json
      ML_MODEL_DIR: /app/models
    volumes:
      - ./data:/data
      - ./ml/models:/app/models
    depends_on:
      backend:
        condition: service_healthy
```

(Match indentation/quoting style to the existing `backend`/`simulator` blocks exactly — read the file first and adjust this snippet to fit rather than pasting verbatim if the existing style differs, e.g. no `container_name`, different healthcheck style, etc.)

- [ ] **Step 3: Rewrite `ml/README.md`**

Replace the placeholder content with a real description: what Phase 2 is (precursor classifier + time-to-impact regressor, LightGBM, offline), the 3 commands to run it end-to-end (`python -m ml.dataset.generate`, `python -m ml.train`, `python -m ml.main`), the `:8200` API (mirroring the root README's table style — see Step 4), and a link to `docs/superpowers/specs/2026-07-13-phase2-predictive-fault-engine-design.md` for the full design rationale. Include a "Running the tests" section: `cd ml && pip install -r requirements.txt && pytest`.

- [ ] **Step 4: Update root `README.md`**

- Check off the roadmap entry: change `2. Predictive fault engine (LSTM/LightGBM, time-to-impact)` to `2. ✅ Predictive fault engine (LightGBM, time-to-impact)` (drop "LSTM" per the design doc's non-goals — it was dropped for hackathon time budget).
- Add a new `### ml / predictive fault engine (\`:8200\`)` subsection under `## API reference`, matching the existing table style:

```markdown
### ml / predictive fault engine (`:8200`)

| Endpoint | Description |
|---|---|
| `GET /predictions` | Current precursor probability + estimated time-to-impact for every node/interface with a warm buffer |
| `GET /predictions/{node_id}` | Current predictions for one node |
| `WS /ws/predictions` | Pushes a prediction update every time a node/interface's buffer is recomputed |
| `GET /health` | Liveness |
```

- Add an `ml/` bullet to the `## Notes for teammates` section, describing it as the Phase 2 predictive fault engine (standalone FastAPI service, LightGBM classifier + regressor, offline).

- [ ] **Step 5: Manual end-to-end verification**

Run each in its own terminal, from repo root, in order:

```bash
# 1. simulator + backend (existing Phase 1 services)
cd simulator && python -m sim.main   # or however Phase 1's README says to start it
cd backend && python -m uvicorn app.main:app --port 8000

# 2. generate labelled data (takes several minutes — 3 scenarios x 6 nodes x ~150s each)
cd ml && python -m ml.dataset.generate

# 3. train
python -m ml.train
# Expected output: "classifier held-out AUC: 0.NN", "regressor held-out MAE: NN.Ns", "saved models to ml/models"

# 4. start the live service
python -m ml.main

# 5. in a 5th terminal, inject a live fault and watch predictions rise
curl -X POST localhost:8100/faults -H "Content-Type: application/json" \
  -d '{"scenario": "congestion_ramp", "node_id": "pe-east", "interface": "TenGigE0/0/0", "params": {"ramp_seconds": 120, "hold_seconds": 30}}'

# poll predictions every few seconds:
curl localhost:8200/predictions/pe-east
```

Expected: `precursor_probability` rises toward 1.0 and `estimated_seconds_to_impact` falls toward 0 as the injected fault's ramp progresses, then the fault clears after `ramp_seconds + hold_seconds` and probability drops back down on subsequent polls.

- [ ] **Step 6: Commit**

```bash
git add ml/Dockerfile docker-compose.yml ml/README.md README.md
git commit -m "ml: wire docker-compose, rewrite docs for Phase 2 predictive fault engine"
```

---

## Self-Review Notes

- **Spec coverage:** Two-model design (Task 5/6), exact label math (Task 3), data-gen harness with retry/always-clear (Task 4), live service WS reconnect + fatal-missing-models + null-on-insufficient-buffer (Task 6), unit tests for features/labels (Tasks 2-3), tiny synthetic end-to-end train check (Task 5), manual verification (Task 7 Step 5), README/roadmap updates (Task 7) — all covered.
- **No placeholders:** every step has complete, concrete code — no TBD/"add error handling"/"similar to Task N" left in.
- **Type consistency checked:** `LiveState.metric_frame`/`event_frame` column names match what `ws_client._handle_message` writes (`ts, node_id, interface, utilization_pct, latency_ms, jitter_ms, packet_loss_pct` and `ts, node_id, severity`); `Predictor.predict`'s return dict shape matches `routes.py`'s and `ws_client.py`'s usage; `feature_names()` is the single source of truth consumed identically by `generate.py`, `train.py`, and `predictor.py`.
- **Known soft spot flagged in-plan:** Task 6 Step 13 acknowledges the WS test's exact `TestClient`/`anyio` threading incantation may need adjusting for the installed Starlette version — implementer should check and adapt rather than treat a mismatch as a blocker.
