# rca/ — Graph Cascade Correlation / Topology-Aware RCA (Phase 3)

Standalone FastAPI service (`:8300`) that turns a flood of scattered symptoms
into **one incident** with a **ranked root cause** and a **predicted cascade** —
fully offline. It is the "why" layer on top of Phase 2's "what/when":

- **Phase 2 (`ml/`, `:8200`)** answers *"which node/interface is ramping toward a
  fault, and in how many seconds?"* — per element, in isolation.
- **Phase 3 (this service, `:8300`)** answers *"these twelve alerts are one
  incident, the root cause is `p-core-1`, and it will spread to the CE-to-CE IPsec
  tunnels and the PE uplinks next."*

The output is a structured **correlated incident** that Phase 4's LLM copilot
narrates and Phase 6's dashboard renders as a cascade view.

See the full design: [`docs/superpowers/specs/2026-07-14-phase3-graph-cascade-rca-design.md`](../docs/superpowers/specs/2026-07-14-phase3-graph-cascade-rca-design.md).

## How it works

1. A **pure-Python topology graph** (`graph.py`) over `data/topology.json` answers
   hop-distance, path, tunnel-dependency, and node-centrality questions. No
   `networkx` — the graph is 6 nodes / 7 links.
2. `symptoms.py` normalizes raw backend events into **anchored, time-decayed
   symptoms** (one per node / BGP session / tunnel). Severity is weighted with an
   exponential decay so the correlator tracks the *active* front of a cascade.
3. `correlate.py` clusters symptoms into **incidents** (temporal proximity), scores
   candidate **root causes** (earliest + centrality + explanatory reach), and
   predicts the ordered **cascade** (blast radius) from the chosen root.
4. `service/` runs it live: a WS client to backend's `/ws/live` buffers events,
   a correlation pass runs on every batch, and incidents are served over HTTP + WS.

Reasoning is **deterministic and explainable** (each root cause carries
`rationale` strings) — deliberately, because it feeds an LLM that must justify the
diagnosis, not a black box.

## Running it

Requires Python 3.11+. From the **repo root**, with Phase 1 backend + simulator
already running (see the root `README.md`):

```bash
pip install -r rca/requirements.txt
python -m rca.main          # listens on :8300
```

Then inject a fault and watch an incident form:

```bash
curl -X POST localhost:8100/faults -H "Content-Type: application/json" \
  -d '{"scenario": "link_degradation", "node_id": "p-core-1",
       "interface": "HundredGigE0/1/0",
       "params": {"ramp_seconds": 120, "hold_seconds": 60}}'

curl localhost:8300/incidents | python -m json.tool
```

## API (`:8300`)

| Endpoint | Description |
|---|---|
| `GET /incidents` | All current incidents (active first, newest first) |
| `GET /incidents/{incident_id}` | One incident (404 if unknown) |
| `WS /ws/incidents` | Pushes `{type:"incident", incident:{...}}` when an incident opens, changes, or resolves |
| `GET /health` | Liveness |

## Optional Phase 2 enrichment

If the `ml` service (`:8200`) is reachable, RCA best-effort folds its
`precursor_probability` / `estimated_seconds_to_impact` into the matching node's
symptom and cascade entries. **This is optional** — if `:8200` is down, RCA still
correlates and localizes on events alone; the time-to-impact fields are simply
`null`. There is no hard runtime dependency on Phase 2.

## Running the tests

```bash
cd rca && pip install -r requirements.txt && pytest
```

(The graph/correlate tests read `data/topology.json` via a repo-root-relative
path, so run pytest from the repo root or with the repo root as the working dir.)
