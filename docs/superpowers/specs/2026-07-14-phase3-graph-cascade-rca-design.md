# Phase 3 — Graph Cascade Correlation / Topology-Aware RCA (design)

Status: proposed (written autonomously per a "continue implementation" directive;
awaiting user review before/after implementation — see note at end)
Date: 2026-07-14

## Purpose

DRISHTI's roadmap Phase 3 adds **topology-aware root-cause analysis**. Phases 1–2
give us live telemetry, events, and per-node/interface precursor predictions, but
they treat every node/interface in isolation. A real fault cascades: a degrading
P-core link starves the PE uplinks that ride it, which threatens the CE-to-CE
IPsec tunnels and the iBGP session between PEs. A NOC operator drowning in a dozen
correlated alerts needs the system to say: **"this is one incident, the root cause
is *here*, and it will spread to *these* elements next."**

Phase 3 consumes symptoms (events + Phase 2 predictions), correlates them against
the topology graph into a single **incident**, ranks the most likely **root-cause
graph element**, and predicts the **cascade** (ordered blast radius). The output is
a structured "correlated incident" object that Phase 4's LLM copilot narrates
(`copilot/README.md`: *"takes a correlated incident (from the Phase-3 graph engine)
and produces an operator-readable root-cause narrative"*) and Phase 6's dashboard
renders as a cascade view.

## Non-goals

- **No ML for RCA.** The correlation and root-cause scoring are deterministic graph
  heuristics, not a learned model. This is deliberate: the output feeds an LLM that
  must explain *why*, so the reasoning must be inspectable and quotable
  (`rationale` strings), not a black-box score. Phase 2 already owns the ML.
- **No fault-type classification beyond what events already carry.** Events from the
  simulator self-label (`details.scenario`); RCA correlates and localizes, it does
  not re-derive the scenario taxonomy.
- **No incident persistence to SQLite (this phase).** Incidents are live in-memory
  state, mirroring how `ml/` holds predictions. A durable incident log can come
  later if a phase needs history; YAGNI for now.
- **No automated remediation.** Phase 3 explains and predicts; Phase 5 (digital twin)
  validates fixes. RCA only produces hypotheses.
- **No new heavy dependencies.** The graph is 6 nodes / 7 links; graph traversal and
  centrality are hand-rolled in pure Python (no `networkx`), keeping the air-gap
  install trivial and matching the minimal-dependency ethos of `backend`/`simulator`.

## Architecture

A new standalone top-level package `rca/` with its own FastAPI service (`:8300`),
mirroring `ml/` (`:8200`) and following the port convention
(backend `:8000` → simulator `:8100` → ml `:8200` → rca `:8300`) and the
`pydantic-settings` env-prefix convention (`RCA_`, matching `DRISHTI_`/`SIM_`/`ML_`):

```
rca/
  config.py        Settings (RCA_ prefix): port, backend URLs, ml_url,
                    topology_path, correlation/cascade knobs
  graph.py         pure-Python topology graph: adjacency, hop distance,
                    tunnel/BGP path dependency, node centrality — pure functions
                    over the topology dict (no service state)
  symptoms.py      normalize raw events (+ optional ml predictions) into Symptom
                    objects anchored to graph elements
  correlate.py     RCA core: cluster symptoms into incidents (temporal), score
                    candidate root causes (topology-aware), predict the cascade
  service/
    state.py        in-memory rolling symptom buffer + current incidents
    ws_client.py    WS client to backend /ws/live (feeds symptoms); best-effort
                     poll of ml /predictions for time-to-impact enrichment
    correlator.py   runs a correlation pass on each batch, diffs incidents,
                     publishes changes
    broadcaster.py  WS fan-out for /ws/incidents (mirrors ml/service/broadcaster.py)
    routes.py       GET /incidents, GET /incidents/{id}, WS /ws/incidents, GET /health
    app.py          FastAPI app + lifespan (WS client background task)
  main.py          uvicorn entrypoint (python -m rca.main)
  tests/           pytest suite (mirrors ml/tests/ layout + conftest anyio backend)
  Dockerfile
  README.md
```

The service is **independently valuable**: events are the primary signal, so RCA
works with only the backend up. Phase 2's ml service is an **optional enrichment** —
if `:8200` is unreachable, RCA still correlates and localizes on events alone and
simply omits `estimated_seconds_to_impact` from cascade entries. This avoids a hard
Phase 3→Phase 2 runtime dependency.

## Graph model (`rca/graph.py`)

Built once from `data/topology.json`. Pure functions, no I/O beyond loading the file:

- **`Graph.from_topology(topology: dict)`** — builds node adjacency from `links`
  (undirected; edge carries `link_id`, `kind`, `bandwidth_mbps`).
- **`neighbors(node)`, `hops(src, dst)`** — BFS shortest hop count between nodes
  (`None` if disconnected).
- **`path_nodes(src, dst)`** — one shortest path node list (for tunnel dependency).
- **`tunnel_path(tunnel)`** — the node list a tunnel rides (`src`→`dst` shortest path);
  a fault on any path node/link threatens the tunnel.
- **`bgp_endpoints(session)` / `sessions_on(node)`** — BGP sessions touching a node.
- **`centrality(node) -> float`** — normalized betweenness: fraction of all
  node-pair shortest paths that pass through `node`. P-core nodes score highest
  (they carry PE↔PE and tunnel traffic), CE nodes lowest. Used to prefer central
  elements as root causes (a core fault explains more than a leaf fault). Computed
  once and cached; 6 nodes makes all-pairs trivial.

Anchor types (what a symptom or cascade entry points at): `node`, `link`, `tunnel`,
`bgp_session`. Each has an `anchor_id` (`node_id` / `link_id` / `tunnel_id` /
`session_id`) and a primary `node_id`.

## Symptoms (`rca/symptoms.py`)

A **Symptom** is the RCA-level unit, aggregating raw events for one anchor over a
window:

```
Symptom {
  anchor_type, anchor_id, node_id,
  first_seen, last_seen: datetime,
  severity_max: str,          # highest severity seen (warning/error/critical)
  severity_weight: float,     # decayed weight (see below)
  event_count: int,
  scenario: str | None,       # from details.scenario if present (self-labeled)
  sample_messages: list[str], # a few representative event messages, newest first
  precursor_probability: float | None,   # from ml enrichment
  estimated_seconds_to_impact: float | None,
}
```

**Anchoring rules** (reliable fields only, no message regex for the anchor):

- `event_type == "bgp"` with `details.session_id` → `bgp_session` anchor
  (`anchor_id = session_id`, `node_id = event.node_id`).
- `event_type == "tunnel"` with a tunnel id in `details` → `tunnel` anchor.
- everything else → `node` anchor (`anchor_id = node_id`). `info`-severity events
  are ignored (fault-expiry noise); only `warning`/`error`/`critical` become symptoms.

**Severity weighting with time decay:** `SEVERITY_WEIGHT = {warning:1, error:3, critical:6}`.
Each event contributes `weight * exp(-age_seconds / DECAY_TAU)` (default `DECAY_TAU=120`);
a symptom's `severity_weight` is the sum over its events. Recent, severe symptoms
dominate — this is what makes the root-cause scoring track the *active* front of a
cascade rather than stale noise.

**ml enrichment:** when the ml service is reachable, per-node/interface predictions
are folded into the matching node's symptom (`precursor_probability`,
`estimated_seconds_to_impact`); absence is fine (`None`).

## Correlation core (`rca/correlate.py`)

Given a list of current Symptoms + the Graph, produce a list of Incidents.

1. **Cluster into incidents (temporal-first).** Two symptoms belong to the same
   incident if their `[first_seen, last_seen]` windows are within `TEMPORAL_WINDOW`
   (default 120s) of each other. Union-find over symptoms. Rationale: in a 6-node
   topology nearly everything is within 2–3 hops, so topology can't *separate*
   incidents — time does. Topology instead provides causal *direction* within an
   incident (below). Symptoms with negligible decayed weight (below
   `MIN_SYMPTOM_WEIGHT`) are dropped before clustering so a resolved incident ages out.

2. **Score candidate root causes (topology-aware).** For each incident, every
   symptomatic anchor is a candidate. Score = weighted sum of three normalized terms:
   - **earliest** (`W_EARLIEST`, default 0.4): causes precede effects — the anchor
     whose `first_seen` is earliest scores 1.0, linearly down to 0 for the latest.
   - **centrality** (`W_CENTRAL`, default 0.3): the anchor's node centrality — a core
     element explains more of the network than a leaf.
   - **explanatory reach** (`W_REACH`, default 0.3): fraction of the *other*
     symptomatic anchors that are topologically dependent on this candidate —
     reachable within `CASCADE_MAX_HOPS`, or (for tunnels/BGP) whose path/endpoints
     include the candidate. A candidate that can causally reach every other symptom
     explains the whole incident.

   Root cause = argmax. **Confidence** = `score_top / (score_top + score_runner_up)`
   (0.5 → tie, →1.0 → dominant), clamped, `1.0` when a single anchor. Each chosen
   term contributes a human-readable `rationale` string (e.g. *"earliest symptom in
   the incident (12s before the next)"*, *"most central element — carries 4 of 6
   shortest paths"*, *"topologically upstream of 3 of 4 other symptoms"*).

3. **Predict the cascade (ordered blast radius).** From the root-cause anchor,
   enumerate at-risk elements not yet symptomatic:
   - **nodes** within `CASCADE_MAX_HOPS` (default 2), ordered by hop distance;
   - **tunnels** whose `tunnel_path` includes the root's node/link;
   - **BGP sessions** on the root node or its immediate neighbors.
   Each cascade entry: `{anchor_type, anchor_id, node_id, hops_from_root,
   at_risk_reason, estimated_seconds_to_impact}` where the time estimate is copied
   from the matching ml prediction when available, else `null`. Ordered by
   `(hops_from_root, -centrality)` — nearest and most-central first.

Incident `severity` is the max symptom severity; `status` is `active` while any
symptom carries weight, `resolved` once all decay below threshold.

## Output schema (the "correlated incident")

```
Incident {
  incident_id: str,                 # stable per symptom-cluster (derived from
                                     #  sorted anchor ids, so it survives updates)
  opened_at, updated_at: datetime,
  status: "active" | "resolved",
  severity: "warning" | "error" | "critical",
  root_cause: {
    anchor_type, anchor_id, node_id,
    confidence: float,              # 0..1
    rationale: list[str],
  },
  symptoms: list[Symptom],          # (schema above)
  cascade: list[CascadeEntry],
}
```

Exposed by the service as:

- `GET /incidents` — all current incidents (active first, newest first).
- `GET /incidents/{incident_id}` — one incident (404 if unknown).
- `WS /ws/incidents` — pushes `{type: "incident", incident: {...}}` whenever an
  incident opens or its root cause/cascade/severity changes.
- `GET /health` — liveness (`{status: "ok", service: "drishti-rca"}`).

## Data flow

1. **`ws_client`** subscribes to backend `ws://localhost:8000/ws/live`, extracts
   `events` from each telemetry batch, and appends them to the in-memory symptom
   buffer (rolling, retention ≈ `TEMPORAL_WINDOW + DECAY_TAU + slack`). On each batch
   it best-effort GETs `:8200/predictions` (short timeout; failure ignored) to
   enrich node symptoms.
2. **`correlator`** rebuilds Symptoms from the buffer, runs `correlate.correlate()`,
   diffs against the previous incident set, updates `state`, and publishes changed
   incidents via the broadcaster.
3. **`routes`** read current incidents from `state`; the WS route registers clients
   with the broadcaster.

Single asyncio loop, one writer (`ws_client`/`correlator`) and read-only routes, so
no locking is needed (same pattern as `ml/service/state.py`).

## Error handling

- Backend WS: reconnect with exponential backoff (reuse the exact pattern from
  `ml/service/ws_client.py`).
- ml enrichment: best-effort with a short timeout; any failure logs at debug and
  yields `None` enrichment — never fatal, never blocks a correlation pass.
- Malformed/unknown events: anchored to their `node_id` at worst; an event whose
  `node_id` isn't in the topology is dropped (logged once), not crashed on.
- Empty buffer / no symptoms: `GET /incidents` returns `[]`, not an error.

## Testing

- **`graph.py`** — hop distances and tunnel paths against the known 6-node topology
  (e.g. `hops(ce-site-a, ce-site-b)` and `tunnel_path(ipsec-a-to-b)` traverse a PE
  and a P-core; `centrality(p-core-1) > centrality(ce-site-a)`).
- **`symptoms.py`** — event lists → expected anchors, severity decay (an old critical
  weighs less than a fresh warning past enough decay), info-events ignored, bgp
  session anchoring from `details.session_id`.
- **`correlate.py`** — hand-built symptom sets → expected clustering, root cause, and
  cascade. Key case: a core-link symptom that started first must win root cause over a
  later PE symptom, and the CE-to-CE tunnels must appear in its cascade.
- **service** — `state` buffer prune/roundtrip; `routes` via `TestClient`
  (health, empty list, seeded incident, 404, WS broadcast — mirroring
  `ml/tests/test_routes.py`); `ws_client._handle_message` updates the buffer and a
  correlation pass publishes an incident (with a fake broadcaster/predictor-enricher,
  mirroring `ml/tests/test_ws_client.py`).
- **Manual e2e** (Task 7 of the plan): backend + simulator + rca up, inject a
  `link_degradation` on `p-core-1`, poll `:8300/incidents` and watch one incident
  form with `p-core-1` as root cause and the IPsec tunnels + PE nodes in the cascade.

## Repo/docs updates

- `rca/README.md`: what Phase 3 is, the run command (`python -m rca.main`), the
  `:8300` API table, the optional ml-enrichment note, and how to run the tests.
- Root `README.md`: check off roadmap item 3, add the `:8300` API reference section
  and an architecture-diagram/`Notes for teammates` bullet.
- `docker-compose.yml`: add an `rca` service (`build: ./rca`, port `8300`, env for
  backend/ml URLs + topology path, `depends_on` backend healthy). Unlike `ml`, `rca`
  needs **no** pre-generated artifacts, so it starts cleanly on a fresh clone.

## Note on process

This spec was authored autonomously under a standing "continue implementation, do
not stop" instruction, so the normal brainstorming approval gate was not run
interactively. Design decisions (standalone `:8300` service, deterministic graph
heuristics over ML, events-primary with optional ml enrichment, no persistence, no
`networkx`) were made to match established repo conventions and the Phase 4 consumer
contract. All are reversible and called out above for review.
