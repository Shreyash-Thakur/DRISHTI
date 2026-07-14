# DRISHTI — Predictive NOC Copilot for Secure MPLS/SD-WAN Operations

Air-gapped copilot that **predicts network failures before they happen**,
explains root cause with a local LLM, and validates fixes on a digital twin.
Everything runs fully offline — zero outbound calls.

## Architecture (full vision)

```
┌────────────────────┐   HTTP POST    ┌──────────────────────────────┐
│  simulator/        │───────────────▶│  backend/  (FastAPI :8000)   │
│  6-node MPLS       │                │  routers → services → repo   │
│  telemetry :8100   │                │            │                 │
│  + fault injection │                │         SQLite (data/)       │
└────────────────────┘                └──────┬────────────┬──────────┘
                                             │ WS /ws/live │ services import
                                             ▼             ▼
                                      ┌────────────┐  ┌───────────────────┐
                                      │ frontend/  │  │ ml/  (Phase 2)    │
                                      │ dashboard  │  │ LightGBM          │
                                      │ (P6) :8080 │  │ precursor detect  │
                                                      └─────────┬─────────┘
                                                                ▼
                                      ┌────────────┐  ┌───────────────────┐
                                      │ rca/ (P3)  │  │ copilot/ (P4)     │
                                      │ topology   │─▶│ Ollama + runbook  │
                                      │ cascade RCA│  │ RAG  :8400        │
                                      │  :8300     │  └─────────┬─────────┘
                                      └────────────┘            ▼
                                                      ┌───────────────────┐
                                                      │ digital twin (P5) │
                                                      └───────────────────┘
```

**Phases 1–4 working now:** simulator → backend → SQLite + live WS (P1);
LightGBM predictive fault engine `ml/` :8200 (P2); topology-aware cascade RCA
`rca/` :8300 (P3); offline LLM copilot `copilot/` :8400 (P4); Containerlab
digital-twin generator `twin/` (P5, generation offline-tested; live deploy needs
the containerlab toolchain); and a self-contained operator dashboard `frontend/`
:8080 (P6). **All six roadmap phases are now implemented** (offline-forced
deviations in P4–P6 are documented in each package's design spec).

## Topology

Six nodes, defined once in [`data/topology.json`](data/topology.json)
(both services load the same file):

| Node | Role | Connects to |
|---|---|---|
| `ce-site-a` | CE (Site A) | `pe-east` |
| `ce-site-b` | CE (Site B) | `pe-west` |
| `pe-east` | PE | `ce-site-a`, `p-core-1`, `p-core-2` |
| `pe-west` | PE | `ce-site-b`, `p-core-1`, `p-core-2` |
| `p-core-1` | P core | full mesh with PEs + `p-core-2` |
| `p-core-2` | P core | full mesh with PEs + `p-core-1` |

Plus 2 IPsec tunnels (Site A ⇄ Site B) and 3 BGP sessions (2 eBGP CE–PE,
1 iBGP VPNv4 PE–PE).

## Quick start

### Option A — Docker (one command)

```bash
docker compose up --build
```

> Air-gap note: the image build needs PyPI once. On the offline network,
> build the images on a connected machine, `docker save`/`docker load` them,
> then `docker compose up` works with zero connectivity.

> **`ml/models/` prerequisite:** the `ml` container will crash-loop on a fresh
> clone / first `docker compose up` — `ml/models/` is git-ignored and must be
> populated before the service can start. From the repo root, with the
> backend + simulator up, run once:
> ```bash
> python -m ml.dataset.generate   # ~30 min, generates labelled training data
> python -m ml.train              # trains + saves models to ml/models/
> ```
> Only then will `ml`'s `Predictor` find `classifier.txt`/`regressor.txt`/
> `features.json` instead of raising `FileNotFoundError` at startup. See
> `ml/README.md` for details.

### Option B — Plain Python (recommended for dev)

Requires Python 3.11+. Two terminals, **both from the repo root**:

```bash
# one-time setup
python -m venv .venv
.venv\Scripts\activate            # Windows   (Linux/mac: source .venv/bin/activate)
pip install -r backend/requirements.txt -r simulator/requirements.txt

# Terminal 1 — backend
uvicorn app.main:app --app-dir backend --port 8000

# Terminal 2 — simulator
set PYTHONPATH=simulator          # Windows   (Linux/mac: export PYTHONPATH=simulator)
python -m sim.main
```

Config lives in `.env` (copy `.env.example`); defaults work out of the box.

### Verify it's alive

```bash
curl localhost:8000/health
curl localhost:8000/topology
curl "localhost:8000/metrics/pe-east?minutes=5"
curl "localhost:8000/events?minutes=60"
```

Interactive API docs: **http://localhost:8000/docs** (backend) and
**http://localhost:8100/docs** (simulator/fault API).

## API reference

### Backend (`:8000`)

| Endpoint | Description |
|---|---|
| `POST /telemetry/ingest` | Simulator pushes a batch every 5 s (validated, stored in SQLite) |
| `GET /topology` | Node/link/tunnel/BGP graph, with per-node interface list |
| `GET /metrics/{node_id}?minutes=15&interface=&limit=` | Recent interface + tunnel timeseries for a node |
| `GET /events?minutes=60&node_id=&event_type=&severity=&limit=` | Recent events, filterable |
| `WS /ws/live` | Every accepted batch pushed as JSON (for the dashboard) |
| `GET /health` | Liveness |

### Simulator / fault injection (`:8100`)

| Endpoint | Description |
|---|---|
| `GET /scenarios` | Available fault scenarios + default params |
| `POST /faults` | Inject a fault (see below) |
| `GET /faults` | Active faults |
| `DELETE /faults/{fault_id}` | Clear one fault |
| `DELETE /faults` | Clear all faults |

### ml / predictive fault engine (`:8200`)

| Endpoint | Description |
|---|---|
| `GET /predictions` | Current precursor probability + estimated time-to-impact for every node/interface with a warm buffer |
| `GET /predictions/{node_id}` | Current predictions for one node |
| `WS /ws/predictions` | Pushes a prediction update every time a node/interface's buffer is recomputed |
| `GET /health` | Liveness |

### rca / cascade RCA (`:8300`)

| Endpoint | Description |
|---|---|
| `GET /incidents` | Current correlated incidents (active first, newest first), each with a ranked root cause + predicted cascade |
| `GET /incidents/{incident_id}` | One incident by id |
| `WS /ws/incidents` | Pushes an incident update whenever one opens, changes, or resolves |
| `GET /health` | Liveness |

### copilot / RCA explainer (`:8400`)

| Endpoint | Description |
|---|---|
| `POST /explain` | Body `{"incident_id": "..."}` (fetched from rca) or `{"incident": {...}}`. Returns a grounded root-cause narrative + the runbooks it used. Needs a local Ollama server + a pulled chat model; degrades to a templated summary (HTTP 200, `llm_available:false`) if Ollama is down |
| `GET /health` | Liveness |

## Injecting faults (precursor patterns for ML)

Faults ramp **gradually** (`ramp_seconds`), hold at full effect
(`hold_seconds`), then auto-expire. The ramp phase is the precursor signature
the Phase-2 models will learn to detect. Events emitted during a fault carry
`details.fault_id`/`details.scenario` — ground-truth labels for free.

```bash
# Congestion ramp on PE-East over 10 minutes
curl -X POST localhost:8100/faults -H "Content-Type: application/json" \
  -d '{"scenario": "congestion_ramp", "node_id": "pe-east"}'

# BGP flap precursor on CE-Site-A (accelerating warnings → flap burst)
curl -X POST localhost:8100/faults -H "Content-Type: application/json" \
  -d '{"scenario": "bgp_flap_precursor", "node_id": "ce-site-a"}'

# Degrading optic on a specific interface, custom ramp
curl -X POST localhost:8100/faults -H "Content-Type: application/json" \
  -d '{"scenario": "link_degradation", "node_id": "p-core-1",
       "interface": "HundredGigE0/1/0",
       "params": {"max_loss_pct": 12, "ramp_seconds": 300}}'
```

| Scenario | Effect during ramp | Events emitted |
|---|---|---|
| `congestion_ramp` | Utilization climbs (default +45 %); latency/jitter/loss follow once the link runs hot | High-util warning at 50 %, queue-drop error at 90 % of ramp |
| `bgp_flap_precursor` | Jitter creep + slight loss | Keepalive-delay/hold-timer warnings at accelerating cadence, then a down/up flap burst |
| `link_degradation` | Packet loss ramps to `max_loss_pct`, jitter ×3 | CRC/input-error syslogs, accelerating |

## Repo layout

```
backend/     FastAPI app — routers → services → repository (SQLite)
simulator/   Telemetry generator + fault-injection API
ml/          Phase 2 predictive fault engine (working) — LightGBM classifier +
             regressor, standalone FastAPI service; see ml/README.md
rca/         Phase 3 topology-aware RCA (working) — pure-Python graph correlation,
             root-cause scoring + cascade prediction, standalone FastAPI service
             (:8300); see rca/README.md
copilot/     Phase 4 offline LLM copilot (working) — local Ollama + TF-IDF runbook
             RAG, standalone FastAPI service (:8400); see copilot/README.md
twin/        Phase 5 digital-twin generator (working) — topology.json →
             Containerlab .clab.yml + per-node FRR configs; see twin/README.md
frontend/    Phase 6 operator dashboard (working) — self-contained static page
             (:8080) consuming all four services; see frontend/README.md
data/runbooks/  operator runbooks (tracked) the copilot retrieves over
frontend/    Phase 6 placeholder — endpoints the dashboard will consume
data/        topology.json (tracked) + drishti.db (generated, git-ignored)
```

## Notes for teammates

- **Predictive fault engine** (`ml/`, Phase 2): LightGBM classifier + regressor,
  standalone FastAPI service (:8200), runs offline on buffered metrics. See
  `ml/README.md` to generate training data via fault injection and train models.
- **Cascade RCA** (`rca/`, Phase 3): topology-aware root-cause + cascade engine,
  standalone FastAPI service (:8300). Correlates events (+ optional Phase 2
  predictions) into incidents with a ranked root cause and predicted blast radius;
  deterministic pure-Python graph heuristics, no extra ML deps. See `rca/README.md`.
- **Offline copilot** (`copilot/`, Phase 4): explains an rca incident as an
  operator narrative via a local Ollama LLM + TF-IDF retrieval over
  `data/runbooks/` (:8400), fully offline. Model-agnostic (`COPILOT_MODEL`); needs
  a local Ollama server. See `copilot/README.md`.
- **Digital twin** (`twin/`, Phase 5): generates a Containerlab lab + FRR configs
  from `data/topology.json` (`python -m twin.generate`) to stand the network up as
  real routers for fix validation. Generation is offline + tested; live
  `containerlab deploy` needs the containerlab toolchain. See `twin/README.md`.
- **Network sim**: scenarios live in `simulator/sim/faults.py` — add a new
  scenario by adding a `SCENARIOS` entry, a branch in `modifiers_for`, and a
  `_tick_<name>` event emitter.
- **Dashboard** (`frontend/`, Phase 6): self-contained static page (`:8080`, no
  build) consuming all four services + the `/ws/live` feed; CORS is open on every
  service. See `frontend/README.md`.
- **Storage**: plain SQLite in WAL mode, no ORM — schema in
  `backend/app/repository/db.py`.

## Roadmap

1. ✅ Telemetry simulator + ingestion backend (this phase)
2. ✅ Predictive fault engine (LightGBM, time-to-impact)
3. ✅ Graph cascade correlation (topology-aware RCA)
4. ✅ Offline LLM copilot (Ollama + local runbook RAG)
5. ✅ Digital twin generator (Containerlab lab + FRR configs; live deploy needs the containerlab toolchain)
6. ✅ Operator dashboard (self-contained static page, no build)
