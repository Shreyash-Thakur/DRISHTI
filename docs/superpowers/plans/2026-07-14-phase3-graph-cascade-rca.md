# Phase 3 Graph Cascade RCA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up `rca/` as a standalone FastAPI service (`:8300`) that correlates live events (+ optional Phase 2 predictions) into topology-aware incidents — each with a ranked root cause and a predicted cascade — fully offline, per `docs/superpowers/specs/2026-07-14-phase3-graph-cascade-rca-design.md`.

**Architecture:** A pure-Python topology graph (`graph.py`) answers hop/path/centrality questions over `data/topology.json`; `symptoms.py` normalizes raw events into anchored, time-decayed symptoms; `correlate.py` clusters symptoms into incidents, scores candidate root causes (earliest + centrality + explanatory reach), and predicts the cascade; a live FastAPI service consumes backend's `/ws/live`, keeps a rolling event buffer, runs a correlation pass per batch, and serves `GET /incidents`, `GET /incidents/{id}`, `WS /ws/incidents`. Service boilerplate mirrors `ml/service/` almost verbatim.

**Tech Stack:** Python 3.11+, FastAPI/uvicorn/pydantic-settings (matching backend/simulator/ml), httpx (ml enrichment + TestClient), websockets (WS client to backend), pytest + anyio (matching `ml/tests/`). No pandas/numpy/lightgbm/networkx — the graph is hand-rolled pure Python.

## Global Constraints

- Zero outbound network calls — `rca` only talks to `localhost` backend (`:8000`) and, best-effort, ml (`:8200`). No telemetry, no runtime downloads.
- New service listens on `:8300`, following backend `:8000` / simulator `:8100` / ml `:8200`.
- Settings via `pydantic-settings`, env prefix `RCA_` (matching `DRISHTI_`/`SIM_`/`ML_`).
- No ORM, no SQLite writes — incidents are in-memory only (like `ml/service/state.py`).
- Dependency pins use loose `>=` minimum-version style (matching `backend`/`ml` requirements).
- Anchor types are exactly four: `node`, `link`, `tunnel`, `bgp_session`.
- ml enrichment is optional and best-effort: any failure reaching `:8200` yields `None` enrichment and must never crash or block a correlation pass.
- Events with severity `info` are ignored for RCA; only `warning`/`error`/`critical` become symptoms. Events whose `node_id` is not in the topology are dropped.

---

## Task 1: `rca/` scaffolding, config, dependencies

**Files:**
- Create: `rca/__init__.py` (empty), `rca/requirements.txt`, `rca/config.py`, `rca/pytest.ini`
- Create: `rca/tests/__init__.py`, `rca/tests/conftest.py`, `rca/tests/test_config.py`
- Create: `rca/service/__init__.py` (empty)

**Interfaces:**
- Produces: `rca.config.Settings` (fields: `port`, `backend_ws_url`, `backend_http_url`, `ml_url`, `topology_path`, `temporal_window_seconds`, `decay_tau_seconds`, `min_symptom_weight`, `cascade_max_hops`, `w_earliest`, `w_central`, `w_reach`) and `rca.config.get_settings() -> Settings` (lru-cached).

- [ ] **Step 1: `rca/requirements.txt`**

```
fastapi>=0.110
uvicorn[standard]>=0.29
pydantic>=2.6
pydantic-settings>=2.2
httpx>=0.27
websockets>=12.0
anyio>=4.0
pytest>=8.0
```

- [ ] **Step 2: `rca/config.py`**

```python
"""Settings for the rca package — the graph loader, correlator, and live
service all read the same Settings so paths/URLs/thresholds stay in sync."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RCA_")

    port: int = 8300
    backend_ws_url: str = "ws://localhost:8000/ws/live"
    backend_http_url: str = "http://localhost:8000"
    ml_url: str = "http://localhost:8200"
    topology_path: Path = Path("data/topology.json")

    temporal_window_seconds: float = 120.0
    decay_tau_seconds: float = 120.0
    min_symptom_weight: float = 0.1
    cascade_max_hops: int = 2
    w_earliest: float = 0.4
    w_central: float = 0.3
    w_reach: float = 0.3


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 3: `rca/pytest.ini`**

```ini
[pytest]
testpaths = tests
```

- [ ] **Step 4: empty `rca/__init__.py`, `rca/tests/__init__.py`, `rca/service/__init__.py`**

- [ ] **Step 5: `rca/tests/conftest.py`** (anyio backend fixture, matching `ml/tests/conftest.py`)

```python
import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 6: `rca/tests/test_config.py`**

```python
from rca.config import Settings, get_settings


def test_default_settings_match_port_convention():
    settings = Settings()
    assert settings.port == 8300
    assert settings.backend_ws_url == "ws://localhost:8000/ws/live"
    assert settings.cascade_max_hops == 2


def test_get_settings_is_cached():
    assert get_settings() is get_settings()
```

- [ ] **Step 7: install + run** — `cd rca && python -m pip install -r requirements.txt && python -m pytest tests/test_config.py -v` → `2 passed`

- [ ] **Step 8: Commit** — `git add rca/__init__.py rca/requirements.txt rca/config.py rca/pytest.ini rca/tests/__init__.py rca/tests/conftest.py rca/tests/test_config.py rca/service/__init__.py && git commit -m "rca: scaffold package, settings, pytest config"`

---

## Task 2: Topology graph (`rca/graph.py`)

**Files:** Create `rca/graph.py`; Test `rca/tests/test_graph.py`.

**Interfaces:**
- Produces: `rca.graph.load_topology(path) -> dict`, `rca.graph.Edge` (frozen dataclass: `link_id, kind, bandwidth_mbps, a, b`), `rca.graph.Graph` with `from_topology(topology) -> Graph`, `from_path(path) -> Graph`, properties `nodes: list[str]`, `tunnels: list[dict]`, `bgp_sessions: list[dict]`, and methods `neighbors(node) -> list[str]`, `hops(src, dst) -> int | None`, `path_nodes(src, dst) -> list[str] | None`, `tunnel_path(tunnel: dict) -> list[str] | None`, `sessions_on(node) -> list[dict]`, `centrality(node) -> float` (normalized 0..1, most-central node = 1.0), `anchor_nodes(anchor_type, anchor_id) -> set[str]`, `anchor_hops(t1, id1, t2, id2) -> int | None`. Consumed by Tasks 3–6.

- [ ] **Step 1: `rca/tests/test_graph.py`**

```python
from pathlib import Path

from rca.graph import Graph

TOPO = Path("data/topology.json")


def _graph() -> Graph:
    return Graph.from_path(TOPO)


def test_neighbors_and_hops_on_known_topology():
    g = _graph()
    assert set(g.neighbors("pe-east")) == {"ce-site-a", "p-core-1", "p-core-2"}
    assert g.hops("pe-east", "pe-east") == 0
    assert g.hops("ce-site-a", "pe-east") == 1
    # ce-a -> pe-east -> p-core -> pe-west -> ce-b
    assert g.hops("ce-site-a", "ce-site-b") == 4


def test_tunnel_path_traverses_pe_and_core():
    g = _graph()
    tunnel = next(t for t in g.tunnels if t["id"] == "ipsec-a-to-b")
    path = g.tunnel_path(tunnel)
    assert path[0] == "ce-site-a" and path[-1] == "ce-site-b"
    assert any(n.startswith("p-core") for n in path)
    assert "pe-east" in path and "pe-west" in path


def test_core_nodes_more_central_than_leaf():
    g = _graph()
    assert g.centrality("p-core-1") > g.centrality("ce-site-a")
    assert g.centrality("ce-site-a") == 0.0  # a leaf is on no intermediate path


def test_anchor_nodes_and_anchor_hops():
    g = _graph()
    assert g.anchor_nodes("node", "pe-east") == {"pe-east"}
    assert g.anchor_nodes("link", "pe-east__p-core-1") == {"pe-east", "p-core-1"}
    assert g.anchor_nodes("bgp_session", "bgp-ce-a__pe-east") == {"ce-site-a", "pe-east"}
    assert set(g.anchor_nodes("tunnel", "ipsec-a-to-b")) >= {"ce-site-a", "ce-site-b"}
    # hop between two node anchors
    assert g.anchor_hops("node", "p-core-1", "node", "pe-east") == 1
```

- [ ] **Step 2: Run to verify fail** — `cd rca && python -m pytest tests/test_graph.py -v` → `ModuleNotFoundError: No module named 'rca.graph'`

- [ ] **Step 3: Implement `rca/graph.py`**

```python
"""Pure-Python topology graph over data/topology.json. No service state, no I/O
beyond loading the file — every method is a deterministic function of the
topology. The graph is 6 nodes / 7 links, so all-pairs shortest paths and
betweenness are trivial to compute eagerly and cache."""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def load_topology(path: Path | str) -> dict:
    return json.loads(Path(path).read_text())


@dataclass(frozen=True)
class Edge:
    link_id: str
    kind: str
    bandwidth_mbps: int
    a: str
    b: str


class Graph:
    def __init__(self, topology: dict) -> None:
        self.topology = topology
        self.nodes: list[str] = [n["id"] for n in topology["nodes"]]
        self._adj: dict[str, list[tuple[str, Edge]]] = {n: [] for n in self.nodes}
        self._edges: list[Edge] = []
        for link in topology.get("links", []):
            a, b = link["a"]["node"], link["b"]["node"]
            edge = Edge(link["id"], link["kind"], link["bandwidth_mbps"], a, b)
            self._edges.append(edge)
            self._adj[a].append((b, edge))
            self._adj[b].append((a, edge))
        self._centrality = self._compute_centrality()

    @classmethod
    def from_topology(cls, topology: dict) -> "Graph":
        return cls(topology)

    @classmethod
    def from_path(cls, path: Path | str) -> "Graph":
        return cls(load_topology(path))

    # -- structure ------------------------------------------------------

    @property
    def tunnels(self) -> list[dict]:
        return self.topology.get("tunnels", [])

    @property
    def bgp_sessions(self) -> list[dict]:
        return self.topology.get("bgp_sessions", [])

    def neighbors(self, node: str) -> list[str]:
        return [peer for peer, _edge in self._adj.get(node, [])]

    # -- traversal ------------------------------------------------------

    def hops(self, src: str, dst: str) -> int | None:
        if src == dst:
            return 0
        if src not in self._adj or dst not in self._adj:
            return None
        seen = {src}
        queue: deque[tuple[str, int]] = deque([(src, 0)])
        while queue:
            node, dist = queue.popleft()
            for peer, _edge in self._adj[node]:
                if peer == dst:
                    return dist + 1
                if peer not in seen:
                    seen.add(peer)
                    queue.append((peer, dist + 1))
        return None

    def path_nodes(self, src: str, dst: str) -> list[str] | None:
        if src == dst:
            return [src]
        if src not in self._adj or dst not in self._adj:
            return None
        prev: dict[str, str | None] = {src: None}
        queue: deque[str] = deque([src])
        while queue:
            node = queue.popleft()
            if node == dst:
                break
            for peer, _edge in self._adj[node]:
                if peer not in prev:
                    prev[peer] = node
                    queue.append(peer)
        if dst not in prev:
            return None
        path: list[str] = []
        cur: str | None = dst
        while cur is not None:
            path.append(cur)
            cur = prev[cur]
        path.reverse()
        return path

    def tunnel_path(self, tunnel: dict) -> list[str] | None:
        return self.path_nodes(tunnel["src"], tunnel["dst"])

    def sessions_on(self, node: str) -> list[dict]:
        return [s for s in self.bgp_sessions if node in (s["a"], s["b"])]

    # -- centrality (normalized betweenness) ----------------------------

    def _compute_centrality(self) -> dict[str, float]:
        counts = {n: 0 for n in self.nodes}
        for i, src in enumerate(self.nodes):
            for dst in self.nodes[i + 1:]:
                path = self.path_nodes(src, dst)
                if path is None or len(path) <= 2:
                    continue
                for mid in path[1:-1]:
                    counts[mid] += 1
        mx = max(counts.values(), default=0)
        if mx == 0:
            return {n: 0.0 for n in self.nodes}
        return {n: counts[n] / mx for n in self.nodes}

    def centrality(self, node: str) -> float:
        return self._centrality.get(node, 0.0)

    # -- anchors --------------------------------------------------------

    def anchor_nodes(self, anchor_type: str, anchor_id: str) -> set[str]:
        if anchor_type == "node":
            return {anchor_id} if anchor_id in self._adj else set()
        if anchor_type == "link":
            for e in self._edges:
                if e.link_id == anchor_id:
                    return {e.a, e.b}
            return set()
        if anchor_type == "tunnel":
            for t in self.tunnels:
                if t["id"] == anchor_id:
                    path = self.tunnel_path(t)
                    return set(path) if path else {t["src"], t["dst"]}
            return set()
        if anchor_type == "bgp_session":
            for s in self.bgp_sessions:
                if s["id"] == anchor_id:
                    return {s["a"], s["b"]}
            return set()
        return set()

    def anchor_hops(self, t1: str, id1: str, t2: str, id2: str) -> int | None:
        ns1 = self.anchor_nodes(t1, id1)
        ns2 = self.anchor_nodes(t2, id2)
        best: int | None = None
        for x in ns1:
            for y in ns2:
                h = self.hops(x, y)
                if h is not None and (best is None or h < best):
                    best = h
        return best
```

- [ ] **Step 4: Run to verify pass** — `cd rca && python -m pytest tests/test_graph.py -v` → `4 passed`

- [ ] **Step 5: Commit** — `git add rca/graph.py rca/tests/test_graph.py && git commit -m "rca: add pure-Python topology graph (hops/paths/centrality/anchors)"`

---

## Task 3: Symptom normalization (`rca/symptoms.py`)

**Files:** Create `rca/symptoms.py`; Test `rca/tests/test_symptoms.py`.

**Interfaces:**
- Produces: `rca.symptoms.parse_ts(value) -> datetime`, `SEVERITY_RANK`, `SEVERITY_WEIGHT`, `rank_to_severity(rank) -> str`, `Symptom` (dataclass, with `to_dict()`), `anchor_for_event(event: dict) -> tuple[str, str]`, `build_symptoms(events: list[dict], as_of: datetime, decay_tau: float, valid_nodes: set[str] | None = None) -> list[Symptom]`, `enrich_with_predictions(symptoms: list[Symptom], predictions: list[dict]) -> None`. Consumed by Task 4 (correlate) and Task 6 (correlator/state).

- [ ] **Step 1: `rca/tests/test_symptoms.py`**

```python
from datetime import datetime, timedelta, timezone

from rca.symptoms import (
    Symptom,
    anchor_for_event,
    build_symptoms,
    enrich_with_predictions,
)

BASE = datetime(2026, 7, 14, tzinfo=timezone.utc)


def _ev(node, sev, etype="syslog", ts=BASE, details=None, msg="m"):
    return {"ts": ts.isoformat(), "node_id": node, "severity": sev,
            "event_type": etype, "message": msg, "details": details or {}}


def test_anchor_for_event_bgp_session_and_node():
    assert anchor_for_event(_ev("pe-east", "warning", "bgp",
                                details={"session_id": "bgp-pe-east__pe-west"})) == (
        "bgp_session", "bgp-pe-east__pe-west")
    assert anchor_for_event(_ev("p-core-1", "error", "syslog")) == ("node", "p-core-1")


def test_build_symptoms_ignores_info_and_groups_by_anchor():
    events = [
        _ev("p-core-1", "info"),        # ignored
        _ev("p-core-1", "warning"),
        _ev("p-core-1", "error"),
    ]
    symptoms = build_symptoms(events, as_of=BASE, decay_tau=120.0)
    assert len(symptoms) == 1
    s = symptoms[0]
    assert s.anchor_type == "node" and s.anchor_id == "p-core-1"
    assert s.event_count == 2          # info dropped
    assert s.severity_max == "error"


def test_build_symptoms_drops_unknown_nodes():
    events = [_ev("ghost-node", "critical")]
    assert build_symptoms(events, as_of=BASE, decay_tau=120.0,
                          valid_nodes={"p-core-1"}) == []


def test_severity_weight_decays_old_critical_below_fresh_warning():
    fresh_warning = build_symptoms([_ev("pe-east", "warning", ts=BASE)],
                                   as_of=BASE, decay_tau=120.0)[0]
    old = BASE - timedelta(seconds=300)
    old_critical = build_symptoms([_ev("pe-west", "critical", ts=old)],
                                  as_of=BASE, decay_tau=120.0)[0]
    assert old_critical.severity_weight < fresh_warning.severity_weight


def test_enrich_with_predictions_folds_into_matching_node():
    symptoms = build_symptoms([_ev("pe-east", "warning")], as_of=BASE, decay_tau=120.0)
    enrich_with_predictions(symptoms, [
        {"node_id": "pe-east", "interface": "eth0",
         "precursor_probability": 0.8, "estimated_seconds_to_impact": 42.0},
    ])
    assert symptoms[0].precursor_probability == 0.8
    assert symptoms[0].estimated_seconds_to_impact == 42.0
```

- [ ] **Step 2: Run to verify fail** — `cd rca && python -m pytest tests/test_symptoms.py -v` → `ModuleNotFoundError`

- [ ] **Step 3: Implement `rca/symptoms.py`**

```python
"""Turns raw backend events into RCA-level Symptoms: one per graph anchor
(node / bgp_session / tunnel), aggregating severity with exponential time decay
so the correlator tracks the *active* front of a cascade, not stale noise."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}
SEVERITY_WEIGHT = {"warning": 1.0, "error": 3.0, "critical": 6.0}
MAX_SAMPLE_MESSAGES = 3


def parse_ts(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def rank_to_severity(rank: int) -> str:
    for name, value in SEVERITY_RANK.items():
        if value == rank:
            return name
    return "info"


@dataclass
class Symptom:
    anchor_type: str
    anchor_id: str
    node_id: str
    first_seen: datetime
    last_seen: datetime
    severity_max: str
    severity_weight: float
    event_count: int
    scenario: str | None = None
    sample_messages: list[str] = field(default_factory=list)
    precursor_probability: float | None = None
    estimated_seconds_to_impact: float | None = None

    def to_dict(self) -> dict:
        return {
            "anchor_type": self.anchor_type,
            "anchor_id": self.anchor_id,
            "node_id": self.node_id,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "severity_max": self.severity_max,
            "severity_weight": round(self.severity_weight, 4),
            "event_count": self.event_count,
            "scenario": self.scenario,
            "sample_messages": self.sample_messages,
            "precursor_probability": self.precursor_probability,
            "estimated_seconds_to_impact": self.estimated_seconds_to_impact,
        }


def anchor_for_event(event: dict) -> tuple[str, str]:
    etype = event.get("event_type")
    details = event.get("details") or {}
    if etype == "bgp" and details.get("session_id"):
        return "bgp_session", str(details["session_id"])
    if etype == "tunnel" and details.get("tunnel_id"):
        return "tunnel", str(details["tunnel_id"])
    return "node", event["node_id"]


def build_symptoms(
    events: list[dict],
    as_of: datetime,
    decay_tau: float,
    valid_nodes: set[str] | None = None,
) -> list[Symptom]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for ev in events:
        if SEVERITY_RANK.get(ev.get("severity", "info"), 0) < SEVERITY_RANK["warning"]:
            continue
        node_id = ev.get("node_id")
        if valid_nodes is not None and node_id not in valid_nodes:
            continue
        anchor = anchor_for_event(ev)
        grouped.setdefault(anchor, []).append(ev)
    return [
        _aggregate(anchor_type, anchor_id, evs, as_of, decay_tau)
        for (anchor_type, anchor_id), evs in grouped.items()
    ]


def _aggregate(
    anchor_type: str, anchor_id: str, events: list[dict], as_of: datetime, decay_tau: float,
) -> Symptom:
    ordered = sorted(events, key=lambda e: parse_ts(e["ts"]))
    times = [parse_ts(e["ts"]) for e in ordered]
    weight = 0.0
    max_rank = 0
    for ev, ts in zip(ordered, times):
        sev = ev.get("severity", "info")
        age = max(0.0, (as_of - ts).total_seconds())
        weight += SEVERITY_WEIGHT.get(sev, 0.0) * math.exp(-age / decay_tau)
        max_rank = max(max_rank, SEVERITY_RANK.get(sev, 0))
    scenario = next(
        ((e.get("details") or {}).get("scenario")
         for e in reversed(ordered) if (e.get("details") or {}).get("scenario")),
        None,
    )
    messages = [e.get("message", "") for e in reversed(ordered)][:MAX_SAMPLE_MESSAGES]
    return Symptom(
        anchor_type=anchor_type,
        anchor_id=anchor_id,
        node_id=ordered[-1]["node_id"],
        first_seen=times[0],
        last_seen=times[-1],
        severity_max=rank_to_severity(max_rank),
        severity_weight=weight,
        event_count=len(ordered),
        scenario=scenario,
        sample_messages=messages,
    )


def enrich_with_predictions(symptoms: list[Symptom], predictions: list[dict]) -> None:
    """Fold ml (:8200) predictions into node symptoms — highest-probability
    interface wins per node. Missing/None predictions leave symptoms untouched."""
    best_by_node: dict[str, dict] = {}
    for pred in predictions:
        prob = pred.get("precursor_probability")
        if prob is None:
            continue
        current = best_by_node.get(pred["node_id"])
        if current is None or prob > (current.get("precursor_probability") or -1.0):
            best_by_node[pred["node_id"]] = pred
    for symptom in symptoms:
        pred = best_by_node.get(symptom.node_id)
        if pred is not None:
            symptom.precursor_probability = pred.get("precursor_probability")
            symptom.estimated_seconds_to_impact = pred.get("estimated_seconds_to_impact")
```

- [ ] **Step 4: Run to verify pass** — `cd rca && python -m pytest tests/test_symptoms.py -v` → `5 passed`

- [ ] **Step 5: Commit** — `git add rca/symptoms.py rca/tests/test_symptoms.py && git commit -m "rca: add event->symptom normalization with severity time-decay"`

---

## Task 4: Correlation core (`rca/correlate.py`)

**Files:** Create `rca/correlate.py`; Test `rca/tests/test_correlate.py`.

**Interfaces:**
- Consumes: `rca.graph.Graph`, `rca.symptoms.Symptom`/`SEVERITY_RANK`/`rank_to_severity`, `rca.config.Settings` (reads `temporal_window_seconds`, `min_symptom_weight`, `cascade_max_hops`, `w_earliest`, `w_central`, `w_reach`).
- Produces: `rca.correlate.Incident` (dataclass with `to_dict()`), `rca.correlate.incident_signature(incident_dict) -> str`, `rca.correlate.correlate(symptoms, graph, settings, node_impact_estimates: dict[str, float | None] | None = None) -> list[Incident]`. Consumed by Task 6.

- [ ] **Step 1: `rca/tests/test_correlate.py`**

```python
from datetime import datetime, timedelta, timezone

from rca.config import Settings
from rca.correlate import correlate, incident_signature
from rca.graph import Graph
from rca.symptoms import Symptom

BASE = datetime(2026, 7, 14, tzinfo=timezone.utc)
GRAPH = Graph.from_path("data/topology.json")
SETTINGS = Settings()


def _sym(anchor_type, anchor_id, node_id, offset_s, weight=5.0, sev="error"):
    t = BASE + timedelta(seconds=offset_s)
    return Symptom(anchor_type, anchor_id, node_id, t, t, sev, weight, 1)


def test_low_weight_symptoms_produce_no_incident():
    weak = _sym("node", "pe-east", "pe-east", 0, weight=0.0)
    assert correlate([weak], GRAPH, SETTINGS) == []


def test_single_symptom_incident_is_its_own_root_cause():
    incidents = correlate([_sym("node", "p-core-1", "p-core-1", 0)], GRAPH, SETTINGS)
    assert len(incidents) == 1
    rc = incidents[0].root_cause
    assert rc["anchor_id"] == "p-core-1"
    assert rc["confidence"] == 1.0


def test_earliest_central_symptom_wins_root_cause_and_tunnels_cascade():
    # core link degrades first, PE shows symptoms 30s later
    core = _sym("node", "p-core-1", "p-core-1", 0)
    pe = _sym("node", "pe-east", "pe-east", 30)
    incidents = correlate([pe, core], GRAPH, SETTINGS)
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.root_cause["node_id"] == "p-core-1"
    assert inc.root_cause["confidence"] > 0.5
    # the CE-to-CE IPsec tunnels ride through p-core-1 -> they're in the cascade
    tunnel_ids = {c["anchor_id"] for c in inc.cascade if c["anchor_type"] == "tunnel"}
    assert "ipsec-a-to-b" in tunnel_ids


def test_temporally_distant_symptoms_split_into_two_incidents():
    a = _sym("node", "pe-east", "pe-east", 0)
    b = _sym("node", "pe-west", "pe-west", 10_000)  # far outside temporal window
    incidents = correlate([a, b], GRAPH, SETTINGS)
    assert len(incidents) == 2


def test_cascade_estimates_pulled_from_node_impact_estimates():
    core = _sym("node", "p-core-1", "p-core-1", 0)
    incidents = correlate([core], GRAPH, SETTINGS,
                          node_impact_estimates={"pe-east": 33.0})
    pe_entries = [c for c in incidents[0].cascade if c["node_id"] == "pe-east"]
    assert pe_entries and pe_entries[0]["estimated_seconds_to_impact"] == 33.0


def test_incident_signature_is_stable_across_timestamp_only_changes():
    inc1 = correlate([_sym("node", "p-core-1", "p-core-1", 0)], GRAPH, SETTINGS)[0]
    inc2 = correlate([_sym("node", "p-core-1", "p-core-1", 5)], GRAPH, SETTINGS)[0]
    assert incident_signature(inc1.to_dict()) == incident_signature(inc2.to_dict())
```

- [ ] **Step 2: Run to verify fail** — `cd rca && python -m pytest tests/test_correlate.py -v` → `ModuleNotFoundError`

- [ ] **Step 3: Implement `rca/correlate.py`**

```python
"""RCA core: cluster symptoms into incidents (temporal proximity), score
candidate root causes (earliest + centrality + explanatory reach), and predict
the cascade (ordered blast radius) from the chosen root. Deterministic and
explainable by design — the output feeds an LLM that must justify 'why'."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from rca.config import Settings
from rca.graph import Graph
from rca.symptoms import SEVERITY_RANK, Symptom, rank_to_severity


@dataclass
class Incident:
    incident_id: str
    opened_at: datetime
    updated_at: datetime
    status: str
    severity: str
    root_cause: dict
    symptoms: list[Symptom]
    cascade: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "incident_id": self.incident_id,
            "opened_at": self.opened_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "status": self.status,
            "severity": self.severity,
            "root_cause": self.root_cause,
            "symptoms": [s.to_dict() for s in self.symptoms],
            "cascade": self.cascade,
        }


def incident_signature(incident: dict) -> str:
    """Shape hash used to decide whether to re-publish — deliberately excludes
    timestamps and continuous weights so it changes only when a symptom, the
    root cause, the cascade, or severity changes."""
    rc = incident["root_cause"]
    parts = [
        f"rc={rc['anchor_type']}:{rc['anchor_id']}",
        "sym=" + ",".join(sorted(
            f"{s['anchor_type']}:{s['anchor_id']}" for s in incident["symptoms"])),
        "cas=" + ",".join(sorted(
            f"{c['anchor_type']}:{c['anchor_id']}" for c in incident["cascade"])),
        f"sev={incident['severity']}",
    ]
    return "|".join(parts)


def _incident_id(group: list[Symptom]) -> str:
    key = "|".join(sorted(f"{s.anchor_type}:{s.anchor_id}" for s in group))
    return "inc-" + hashlib.sha1(key.encode()).hexdigest()[:8]


def _find(parent: list[int], x: int) -> int:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _windows_close(a: Symptom, b: Symptom, window: float) -> bool:
    latest_start = max(a.first_seen, b.first_seen)
    earliest_end = min(a.last_seen, b.last_seen)
    if latest_start <= earliest_end:
        return True
    return (latest_start - earliest_end).total_seconds() <= window


def _cluster(symptoms: list[Symptom], window: float) -> list[list[Symptom]]:
    parent = list(range(len(symptoms)))
    for i in range(len(symptoms)):
        for j in range(i + 1, len(symptoms)):
            if _windows_close(symptoms[i], symptoms[j], window):
                parent[_find(parent, i)] = _find(parent, j)
    groups: dict[int, list[Symptom]] = {}
    for i, symptom in enumerate(symptoms):
        groups.setdefault(_find(parent, i), []).append(symptom)
    return list(groups.values())


def _anchor_centrality(graph: Graph, symptom: Symptom) -> float:
    nodes = graph.anchor_nodes(symptom.anchor_type, symptom.anchor_id)
    return max((graph.centrality(n) for n in nodes), default=0.0)


def _score_root_cause(
    group: list[Symptom], graph: Graph, settings: Settings,
) -> tuple[Symptom, float, float, list[str]]:
    """Returns (root_symptom, top_score, runner_up_score, rationale)."""
    firsts = [s.first_seen for s in group]
    t_min, t_max = min(firsts), max(firsts)
    span = (t_max - t_min).total_seconds() or 1.0
    scored: list[tuple[Symptom, float, list[str]]] = []
    for symptom in group:
        others = [o for o in group if o is not symptom]
        earliest = 1.0 - ((symptom.first_seen - t_min).total_seconds() / span)
        central = _anchor_centrality(graph, symptom)
        if others:
            reachable = sum(
                1 for o in others
                if (h := graph.anchor_hops(symptom.anchor_type, symptom.anchor_id,
                                           o.anchor_type, o.anchor_id)) is not None
                and h <= settings.cascade_max_hops
            )
            reach = reachable / len(others)
        else:
            reach = 1.0
        score = (settings.w_earliest * earliest
                 + settings.w_central * central
                 + settings.w_reach * reach)
        rationale: list[str] = []
        if earliest >= 0.999 and others:
            rationale.append("earliest symptom in the incident")
        if central >= 0.5:
            rationale.append(f"central topology element (centrality {central:.2f})")
        if others and reach > 0:
            rationale.append(
                f"topologically upstream of {int(round(reach * len(others)))} "
                f"of {len(others)} other symptoms")
        scored.append((symptom, score, rationale))
    scored.sort(key=lambda item: item[1], reverse=True)
    top = scored[0]
    runner_up_score = scored[1][1] if len(scored) > 1 else 0.0
    rationale = top[2] or ["only / strongest symptom in the incident"]
    return top[0], top[1], runner_up_score, rationale


def _predict_cascade(
    root: Symptom, group: list[Symptom], graph: Graph, settings: Settings,
    node_impact_estimates: dict[str, float | None],
) -> list[dict]:
    symptomatic = {(s.anchor_type, s.anchor_id) for s in group}
    root_nodes = graph.anchor_nodes(root.anchor_type, root.anchor_id)
    entries: list[dict] = []

    for node in graph.nodes:
        if ("node", node) in symptomatic or node in root_nodes:
            continue
        distances = [h for rn in root_nodes if (h := graph.hops(rn, node)) is not None]
        if not distances:
            continue
        hops = min(distances)
        if hops == 0 or hops > settings.cascade_max_hops:
            continue
        entries.append({
            "anchor_type": "node", "anchor_id": node, "node_id": node,
            "hops_from_root": hops,
            "at_risk_reason": f"{hops} hop(s) downstream of the root cause",
            "estimated_seconds_to_impact": node_impact_estimates.get(node),
        })

    for tunnel in graph.tunnels:
        if ("tunnel", tunnel["id"]) in symptomatic:
            continue
        path = graph.tunnel_path(tunnel) or []
        if root_nodes & set(path):
            entries.append({
                "anchor_type": "tunnel", "anchor_id": tunnel["id"],
                "node_id": tunnel.get("src", ""),
                "hops_from_root": 0,
                "at_risk_reason": "rides the affected path: " + " -> ".join(path),
                "estimated_seconds_to_impact": None,
            })

    candidate_nodes = set(root_nodes)
    for rn in root_nodes:
        candidate_nodes.update(graph.neighbors(rn))
    for session in graph.bgp_sessions:
        if ("bgp_session", session["id"]) in symptomatic:
            continue
        if candidate_nodes & {session["a"], session["b"]}:
            entries.append({
                "anchor_type": "bgp_session", "anchor_id": session["id"],
                "node_id": session["a"],
                "hops_from_root": 0,
                "at_risk_reason": f"BGP session on/adjacent to the root cause",
                "estimated_seconds_to_impact": None,
            })

    entries.sort(key=lambda e: (e["hops_from_root"], -graph.centrality(e["node_id"])))
    return entries


def _build_incident(
    group: list[Symptom], graph: Graph, settings: Settings,
    node_impact_estimates: dict[str, float | None],
) -> Incident:
    root, top, runner_up, rationale = _score_root_cause(group, graph, settings)
    confidence = 1.0 if len(group) == 1 else top / (top + runner_up) if (top + runner_up) else 0.5
    severity_rank = max(SEVERITY_RANK.get(s.severity_max, 0) for s in group)
    cascade = _predict_cascade(root, group, graph, settings, node_impact_estimates)
    return Incident(
        incident_id=_incident_id(group),
        opened_at=min(s.first_seen for s in group),
        updated_at=max(s.last_seen for s in group),
        status="active",
        severity=rank_to_severity(severity_rank),
        root_cause={
            "anchor_type": root.anchor_type,
            "anchor_id": root.anchor_id,
            "node_id": root.node_id,
            "confidence": round(confidence, 3),
            "rationale": rationale,
        },
        symptoms=sorted(group, key=lambda s: s.first_seen),
        cascade=cascade,
    )


def correlate(
    symptoms: list[Symptom], graph: Graph, settings: Settings,
    node_impact_estimates: dict[str, float | None] | None = None,
) -> list[Incident]:
    active = [s for s in symptoms if s.severity_weight >= settings.min_symptom_weight]
    if not active:
        return []
    incidents = [
        _build_incident(group, graph, settings, node_impact_estimates or {})
        for group in _cluster(active, settings.temporal_window_seconds)
    ]
    incidents.sort(key=lambda i: i.opened_at, reverse=True)
    return incidents
```

- [ ] **Step 4: Run to verify pass** — `cd rca && python -m pytest tests/test_correlate.py -v` → `6 passed`

- [ ] **Step 5: Commit** — `git add rca/correlate.py rca/tests/test_correlate.py && git commit -m "rca: add incident clustering, root-cause scoring, cascade prediction"`

---

## Task 5: Service state + broadcaster (`rca/service/state.py`, `rca/service/broadcaster.py`)

**Files:** Create `rca/service/state.py`, `rca/service/broadcaster.py`; Test `rca/tests/test_state.py`.

**Interfaces:**
- Produces:
  - `rca.service.state.RcaState(retention: timedelta)` — attributes `predictions: list[dict]`; methods `add_event(event: dict, now: datetime)`, `current_events() -> list[dict]`, `set_incidents(incidents: list[dict])`, `all_incidents() -> list[dict]`, `get_incident(incident_id) -> dict | None`.
  - `rca.service.broadcaster.Broadcaster` — `register(ws)`, `unregister(ws)`, `publish(incident: dict)` (async; sends `{"type": "incident", "incident": ...}`). Identical shape to `ml/service/broadcaster.py` except the message key.

- [ ] **Step 1: `rca/tests/test_state.py`**

```python
from datetime import datetime, timedelta, timezone

from rca.service.state import RcaState

BASE = datetime(2026, 7, 14, tzinfo=timezone.utc)


def _ev(node, ts):
    return {"ts": ts.isoformat(), "node_id": node, "severity": "warning",
            "event_type": "syslog", "message": "m", "details": {}}


def test_add_event_prunes_beyond_retention():
    state = RcaState(retention=timedelta(seconds=100))
    state.add_event(_ev("pe-east", BASE), BASE)
    later = BASE + timedelta(seconds=101)
    state.add_event(_ev("pe-west", later), later)
    events = state.current_events()
    assert len(events) == 1
    assert events[0]["node_id"] == "pe-west"


def test_set_and_get_incidents_roundtrip():
    state = RcaState(retention=timedelta(seconds=100))
    assert state.all_incidents() == []
    inc = {"incident_id": "inc-1", "status": "active"}
    state.set_incidents([inc])
    assert state.all_incidents() == [inc]
    assert state.get_incident("inc-1") == inc
    assert state.get_incident("nope") is None
```

- [ ] **Step 2: Run to verify fail** — `cd rca && python -m pytest tests/test_state.py -v`

- [ ] **Step 3: Implement `rca/service/state.py`**

```python
"""In-memory rolling event buffer + latest ml predictions + current incidents.
Single instance per process; the WS client is the only writer and the routes are
read-only, both on one asyncio loop, so no locking is needed (mirrors
ml/service/state.py)."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

from rca.symptoms import parse_ts


class RcaState:
    def __init__(self, retention: timedelta) -> None:
        self.retention = retention
        self.events: deque[dict] = deque()
        self.predictions: list[dict] = []
        self.incidents: dict[str, dict] = {}

    def add_event(self, event: dict, now: datetime) -> None:
        self.events.append(event)
        cutoff = now - self.retention
        while self.events and parse_ts(self.events[0]["ts"]) < cutoff:
            self.events.popleft()

    def current_events(self) -> list[dict]:
        return list(self.events)

    def set_incidents(self, incidents: list[dict]) -> None:
        self.incidents = {inc["incident_id"]: inc for inc in incidents}

    def all_incidents(self) -> list[dict]:
        return list(self.incidents.values())

    def get_incident(self, incident_id: str) -> dict | None:
        return self.incidents.get(incident_id)
```

- [ ] **Step 4: Implement `rca/service/broadcaster.py`** (mirrors `ml/service/broadcaster.py`, message key `"incident"`)

```python
"""WS fan-out for /ws/incidents, mirroring ml/service/broadcaster.py."""
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

    async def publish(self, incident: dict) -> None:
        async with self._lock:
            clients = list(self._clients)
        dead = []
        for ws in clients:
            try:
                await ws.send_json({"type": "incident", "incident": incident})
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)
```

- [ ] **Step 5: Run to verify pass** — `cd rca && python -m pytest tests/test_state.py -v` → `2 passed`

- [ ] **Step 6: Commit** — `git add rca/service/state.py rca/service/broadcaster.py rca/tests/test_state.py && git commit -m "rca: add live state buffer and incident broadcaster"`

---

## Task 6: Correlator, WS client, routes, app, entrypoint

**Files:** Create `rca/service/correlator.py`, `rca/service/ws_client.py`, `rca/service/routes.py`, `rca/service/app.py`, `rca/main.py`; Test `rca/tests/test_routes.py`, `rca/tests/test_ws_client.py`.

**Interfaces:**
- `rca.service.correlator.Correlator(graph, settings, state, broadcaster)` — `compute(now) -> list[dict]` (builds symptoms from `state.current_events()`, enriches with `state.predictions`, calls `correlate`, returns incident dicts), `async run_pass(now)` (diffs vs `state.incidents`, publishes changed + resolved).
- `rca.service.ws_client._handle_message(raw, state, correlator, fetch_predictions)` (async) and `run_ws_client(ws_url, state, correlator, fetch_predictions)` (async, reconnect w/ backoff). `fetch_predictions` is an async `() -> list[dict] | None`.
- `rca.service.routes.router` — `GET /health`, `GET /incidents`, `GET /incidents/{incident_id}`, `WS /ws/incidents`.
- `rca.service.app.create_app() -> FastAPI` + module-level `app`; `rca.main.main()`.

- [ ] **Step 1: `rca/tests/test_ws_client.py`**

```python
import pytest

from rca.config import Settings
from rca.graph import Graph
from rca.service.broadcaster import Broadcaster
from rca.service.correlator import Correlator
from rca.service.state import RcaState
from rca.service.ws_client import _handle_message
from datetime import timedelta

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
```

- [ ] **Step 2: Implement `rca/service/correlator.py`**

```python
"""Runs a correlation pass over the current event buffer, diffs the result
against the previously-published incidents, and pushes opened/changed/resolved
incidents to WS subscribers."""
from __future__ import annotations

from datetime import datetime

from rca.correlate import correlate, incident_signature
from rca.graph import Graph
from rca.config import Settings
from rca.service.broadcaster import Broadcaster
from rca.service.state import RcaState
from rca.symptoms import build_symptoms, enrich_with_predictions


class Correlator:
    def __init__(self, graph: Graph, settings: Settings, state: RcaState, broadcaster: Broadcaster) -> None:
        self.graph = graph
        self.settings = settings
        self.state = state
        self.broadcaster = broadcaster

    def compute(self, now: datetime) -> list[dict]:
        symptoms = build_symptoms(
            self.state.current_events(), now, self.settings.decay_tau_seconds,
            valid_nodes=set(self.graph.nodes),
        )
        enrich_with_predictions(symptoms, self.state.predictions)
        node_estimates = {
            p["node_id"]: p.get("estimated_seconds_to_impact")
            for p in self.state.predictions
            if p.get("estimated_seconds_to_impact") is not None
        }
        incidents = correlate(symptoms, self.graph, self.settings, node_estimates)
        return [inc.to_dict() for inc in incidents]

    async def run_pass(self, now: datetime) -> None:
        new_incidents = self.compute(now)
        new_map = {inc["incident_id"]: inc for inc in new_incidents}
        old_map = self.state.incidents

        changed = [
            inc for iid, inc in new_map.items()
            if iid not in old_map or incident_signature(old_map[iid]) != incident_signature(inc)
        ]
        resolved_ids = [iid for iid in old_map if iid not in new_map]

        self.state.set_incidents(new_incidents)

        for inc in changed:
            await self.broadcaster.publish(inc)
        for iid in resolved_ids:
            resolved = {**old_map[iid], "status": "resolved", "updated_at": now.isoformat()}
            await self.broadcaster.publish(resolved)
```

- [ ] **Step 3: Implement `rca/service/ws_client.py`** (reconnect/backoff pattern copied from `ml/service/ws_client.py`)

```python
"""WS client to backend's /ws/live: buffers events, best-effort enriches with ml
predictions, and runs a correlation pass on every incoming batch. Exponential
backoff reconnect on disconnect (mirrors ml/service/ws_client.py)."""
from __future__ import annotations

import asyncio
import json
import logging

import websockets

from rca.symptoms import parse_ts

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 30


async def _handle_message(raw: str, state, correlator, fetch_predictions) -> None:
    message = json.loads(raw)
    if message.get("type") != "telemetry":
        return
    batch = message["batch"]
    now = parse_ts(batch["sent_at"])
    for event in batch.get("events", []):
        state.add_event(event, now)
    predictions = await fetch_predictions()
    if predictions is not None:
        state.predictions = predictions
    await correlator.run_pass(now)


async def run_ws_client(ws_url: str, state, correlator, fetch_predictions) -> None:
    backoff = 1
    while True:
        try:
            async with websockets.connect(ws_url) as ws:
                logger.info("connected to %s", ws_url)
                backoff = 1
                async for raw in ws:
                    await _handle_message(raw, state, correlator, fetch_predictions)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("ws connection to %s lost, retrying in %ss", ws_url, backoff, exc_info=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
```

- [ ] **Step 4: Run WS-client tests** — `cd rca && python -m pytest tests/test_ws_client.py -v` → `2 passed`

- [ ] **Step 5: `rca/tests/test_routes.py`**

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rca.service.broadcaster import Broadcaster
from rca.service.routes import router
from rca.service.state import RcaState
from datetime import timedelta


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.rca_state = RcaState(retention=timedelta(seconds=300))
    app.state.broadcaster = Broadcaster()
    return TestClient(app)


def test_health():
    response = _client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "drishti-rca"}


def test_incidents_empty_list_initially():
    response = _client().get("/incidents")
    assert response.status_code == 200
    assert response.json() == []


def test_incident_404_for_unknown_id():
    assert _client().get("/incidents/nope").status_code == 404


def test_incident_returns_seeded():
    client = _client()
    client.app.state.rca_state.set_incidents([{"incident_id": "inc-1", "status": "active"}])
    response = client.get("/incidents/inc-1")
    assert response.status_code == 200
    assert response.json()["incident_id"] == "inc-1"


def test_ws_incidents_receives_broadcast():
    client = _client()
    with client.websocket_connect("/ws/incidents") as ws:
        import anyio

        async def _publish():
            await client.app.state.broadcaster.publish({"incident_id": "inc-9"})

        anyio.from_thread.run(_publish)
        message = ws.receive_json()
        assert message == {"type": "incident", "incident": {"incident_id": "inc-9"}}
```

- [ ] **Step 6: Implement `rca/service/routes.py`** (mirrors `ml/service/routes.py`)

```python
"""HTTP + WS routes for the rca service. Reads shared state off request.app.state
/ ws.app.state, set up in rca/service/app.py's lifespan."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "drishti-rca"}


@router.get("/incidents")
async def all_incidents(request: Request) -> list[dict]:
    incidents = request.app.state.rca_state.all_incidents()
    return sorted(
        incidents,
        key=lambda i: (i.get("status") != "active", i.get("opened_at", "")),
        reverse=False,
    )


@router.get("/incidents/{incident_id}")
async def one_incident(incident_id: str, request: Request) -> dict:
    incident = request.app.state.rca_state.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"no incident with id={incident_id!r}")
    return incident


@router.websocket("/ws/incidents")
async def ws_incidents(ws: WebSocket) -> None:
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

- [ ] **Step 7: Run routes tests** — `cd rca && python -m pytest tests/test_routes.py -v` → `5 passed` (if the WS test's `anyio.from_thread.run` incantation mismatches the installed Starlette, adapt exactly as `ml/tests/test_routes.py` does — the goal is: publish while a WS client is connected, assert `receive_json()` gets it).

- [ ] **Step 8: Implement `rca/service/app.py`**

```python
"""FastAPI app: builds the graph + state at startup, runs the backend WS client
as a background task (with best-effort ml enrichment), exposes incident routes."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
from fastapi import FastAPI

from rca.config import get_settings
from rca.graph import Graph
from rca.service.broadcaster import Broadcaster
from rca.service.correlator import Correlator
from rca.service.routes import router
from rca.service.state import RcaState
from rca.service.ws_client import run_ws_client

logger = logging.getLogger(__name__)


def _make_fetch_predictions(client: httpx.AsyncClient, ml_url: str):
    async def fetch_predictions():
        try:
            response = await client.get(f"{ml_url}/predictions", timeout=2.0)
            response.raise_for_status()
            return response.json()
        except Exception:
            logger.debug("ml enrichment unavailable at %s", ml_url, exc_info=True)
            return None
    return fetch_predictions


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    retention = timedelta(
        seconds=settings.temporal_window_seconds + settings.decay_tau_seconds + 60)
    app.state.rca_state = RcaState(retention=retention)
    app.state.broadcaster = Broadcaster()
    graph = Graph.from_path(settings.topology_path)
    correlator = Correlator(graph, settings, app.state.rca_state, app.state.broadcaster)
    client = httpx.AsyncClient()
    fetch_predictions = _make_fetch_predictions(client, settings.ml_url)
    task = asyncio.create_task(
        run_ws_client(settings.backend_ws_url, app.state.rca_state, correlator, fetch_predictions))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="drishti-rca", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
```

- [ ] **Step 9: Implement `rca/main.py`**

```python
"""Entrypoint: `python -m rca.main` (or `uvicorn rca.service.app:app --port 8300`)."""
from __future__ import annotations

import uvicorn

from rca.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run("rca.service.app:app", host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 10: Run the full rca suite** — `cd rca && python -m pytest -v` → all pass across test_config/test_graph/test_symptoms/test_correlate/test_state/test_ws_client/test_routes.

- [ ] **Step 11: Commit** — `git add rca/service/correlator.py rca/service/ws_client.py rca/service/routes.py rca/service/app.py rca/main.py rca/tests/test_routes.py rca/tests/test_ws_client.py && git commit -m "rca: add correlator, backend WS client, routes, app, entrypoint"`

---

## Task 7: Docker wiring + docs + manual verification

**Files:** Create `rca/Dockerfile`, `rca/README.md`; Modify `docker-compose.yml`, root `README.md`.

- [ ] **Step 1: `rca/Dockerfile`** (match `ml/Dockerfile` — read it first; expected shape:)

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./rca

ENV PYTHONPATH=/app

EXPOSE 8300

CMD ["python", "-m", "rca.main"]
```

(Read `ml/Dockerfile` and match its exact COPY layout / PYTHONPATH so `import rca` resolves the same way `import ml` does in that image.)

- [ ] **Step 2: Modify `docker-compose.yml`** — add an `rca` service after `ml`, matching the existing block style (read the file first). Expected:

```yaml
  rca:
    build: ./rca
    container_name: drishti-rca
    ports:
      - "8300:8300"
    environment:
      RCA_BACKEND_WS_URL: ws://backend:8000/ws/live
      RCA_BACKEND_HTTP_URL: http://backend:8000
      RCA_ML_URL: http://ml:8200
      RCA_TOPOLOGY_PATH: /data/topology.json
    volumes:
      - ./data:/data
    depends_on:
      backend:
        condition: service_healthy
```

(Unlike `ml`, `rca` needs no pre-generated artifacts — it starts cleanly on a fresh clone. Do NOT add a hard `depends_on: ml` beyond what keeps enrichment best-effort; RCA must run even if ml is down.)

- [ ] **Step 3: Write `rca/README.md`** — what Phase 3 is (topology-aware RCA: correlate → root cause → cascade, deterministic graph heuristics, offline), the run command (`python -m rca.main`, from repo root with backend + simulator up), the `:8300` API table, the optional ml-enrichment note, "Running the tests" (`cd rca && pip install -r requirements.txt && pytest`), and a link to the design spec.

- [ ] **Step 4: Update root `README.md`** — check off roadmap item 3 → `3. ✅ Graph cascade correlation (topology-aware RCA)`; add a `### rca / cascade RCA (\`:8300\`)` API-reference subsection (table: `GET /incidents`, `GET /incidents/{node_id}` → correct to `{incident_id}`, `WS /ws/incidents`, `GET /health`); add a `Notes for teammates` bullet + update the architecture diagram's Phase-3 box.

- [ ] **Step 5: Manual end-to-end verification** (backend + simulator running, from repo root):

```bash
# start rca
python -m rca.main    # listens on :8300

# inject a core-link degradation — the root cause should localize to p-core-1
curl -X POST localhost:8100/faults -H "Content-Type: application/json" \
  -d '{"scenario": "link_degradation", "node_id": "p-core-1", "interface": "HundredGigE0/1/0", "params": {"ramp_seconds": 120, "hold_seconds": 60}}'

# poll incidents — within ~1-2 batches an incident should form
curl localhost:8300/incidents | python -m json.tool
```

Expected: one incident with `root_cause.node_id == "p-core-1"`, `symptoms` containing the CRC-error syslogs, and `cascade` listing the CE-to-CE IPsec tunnels (they ride p-core-1) plus neighboring PE nodes. As the fault expires, the incident transitions to `resolved` on the WS feed.

- [ ] **Step 6: Commit** — `git add rca/Dockerfile rca/README.md docker-compose.yml README.md && git commit -m "rca: wire docker-compose, docs for Phase 3 cascade RCA"`

---

## Self-Review Notes

- **Spec coverage:** standalone `:8300` service (Task 1/6), pure-Python graph incl. centrality + anchor mapping (Task 2), symptom anchoring + severity time-decay + ml enrichment (Task 3), temporal clustering + 3-term root-cause scoring + cascade prediction (Task 4), in-memory state + broadcaster (Task 5), correlator diff/publish + backend WS client + routes + app (Task 6), Docker/docs/manual-verify (Task 7) — all spec sections mapped.
- **No placeholders:** every code step has complete source; service boilerplate that mirrors `ml/` is written out in full here rather than referenced.
- **Type consistency:** `Graph.anchor_nodes`/`anchor_hops` signatures match `correlate.py`'s calls; `Symptom.to_dict()` keys match `incident_signature`'s reads (`anchor_type`/`anchor_id`); `correlate()` return (`list[Incident]`) is `.to_dict()`-ed by `Correlator.compute` before hitting state/routes; `RcaState` attribute/method names match `Correlator` and `routes` usage; `_handle_message` args match `run_ws_client`'s call and `test_ws_client`'s call.
- **Known soft spot:** Task 6 Step 7 flags the same `TestClient`/`anyio` WS incantation caveat as the Phase 2 plan — adapt to the installed Starlette if needed.
