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
                                      │ (Phase 6)  │  │ LightGBM          │
                                      └────────────┘  │ precursor detect  │
                                                      └─────────┬─────────┘
                                                                ▼
                                      ┌────────────┐  ┌───────────────────┐
                                      │ digital    │  │ copilot/ (Phase 4)│
                                      │ twin (P5)  │◀─│ Ollama + RAG      │
                                      └────────────┘  └───────────────────┘
```

**Phase 1 (this repo, working now):** simulator → backend → SQLite + live WS.
Phases 2–6 have placeholder packages with integration notes in their READMEs.

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
copilot/     Phase 4 placeholder — Ollama + RAG integration notes
frontend/    Phase 6 placeholder — endpoints the dashboard will consume
data/        topology.json (tracked) + drishti.db (generated, git-ignored)
```

## Notes for teammates

- **Predictive fault engine** (`ml/`, Phase 2): LightGBM classifier + regressor,
  standalone FastAPI service (:8200), runs offline on buffered metrics. See
  `ml/README.md` to generate training data via fault injection and train models.
- **Network sim**: scenarios live in `simulator/sim/faults.py` — add a new
  scenario by adding a `SCENARIOS` entry, a branch in `modifiers_for`, and a
  `_tick_<name>` event emitter.
- **Frontend** (`frontend/README.md`): consume the four GET endpoints + the
  WS feed; CORS is open.
- **Storage**: plain SQLite in WAL mode, no ORM — schema in
  `backend/app/repository/db.py`.

## Roadmap

1. ✅ Telemetry simulator + ingestion backend (this phase)
2. ✅ Predictive fault engine (LightGBM, time-to-impact)
3. Graph cascade correlation (topology-aware RCA)
4. Offline LLM copilot (Ollama Mistral 7B + ChromaDB RAG)
5. Digital twin validation (Containerlab)
6. Operator dashboard (React)
