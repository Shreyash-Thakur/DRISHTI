# DRISHTI — Predictive NOC Copilot for Secure MPLS/SD-WAN Operations

**DRISHTI** is an air-gapped copilot for network operations centers that:

- **Predicts network failures before they happen** (LightGBM precursor detection)
- **Explains root cause** with a local, offline LLM grounded in operator runbooks
- **Validates fixes on a digital twin** before they touch production

Everything runs fully offline — **zero outbound calls**, suitable for secure/air-gapped networks.

**Status: all six roadmap phases are implemented and working.** Offline-forced deviations in Phases 4–6 (e.g. LLM degradation without Ollama, digital-twin generation vs. live deploy) are documented in each package's own design spec.

---

## Table of contents

- [Architecture](#architecture)
- [Topology](#topology)
- [Quick start](#quick-start)
  - [Option A — Docker Compose](#option-a--docker-docker-compose)
  - [Option B — Plain Python (dev)](#option-b--plain-python-recommended-for-dev)
  - [Option C — All-in-one container](#option-c--all-in-one-single-container)
  - [Verifying it's alive](#verifying-its-alive)
- [Running a demo](#running-a-demo)
  - [End-to-end, no services required](#end-to-end-demo-no-services-required)
  - [Live-stack smoke test](#live-stack-smoke-test-services-must-be-up)
  - [Edge-case scenarios](#edge-case-simulations-you-can-run)
- [Injecting faults](#injecting-faults-precursor-patterns-for-ml)
- [API reference](#api-reference)
- [Repo layout](#repo-layout)
- [Notes for teammates](#notes-for-teammates)
- [Roadmap](#roadmap)

---

## Architecture

```
┌────────────────────┐   HTTP POST    ┌──────────────────────────────┐
│  simulator/         │───────────────▶│  backend/  (FastAPI :8000)   │
│  6-node MPLS        │                │  routers → services → repo   │
│  telemetry :8100    │                │            │                 │
│  + fault injection   │                │         SQLite (data/)       │
└────────────────────┘                └──────┬────────────┬──────────┘
                                              │ WS /ws/live │ services import
                                              ▼             ▼
                                       ┌────────────┐  ┌───────────────────┐
                                       │ frontend/  │  │ ml/  (Phase 2)     │
                                       │ dashboard  │  │ LightGBM           │
                                       │ (P6) :8080 │  │ precursor detect   │
                                       └────────────┘  └─────────┬─────────┘
                                                                  ▼
                                       ┌────────────┐  ┌───────────────────┐
                                       │ rca/ (P3)  │  │ copilot/ (P4)      │
                                       │ topology   │─▶│ Ollama + runbook   │
                                       │ cascade RCA│  │ RAG  :8400         │
                                       │  :8300     │  └─────────┬─────────┘
                                       └────────────┘            ▼
                                                       ┌───────────────────┐
                                                       │ digital twin (P5) │
                                                       └───────────────────┘
```

| Phase | Package | Port | Role |
|---|---|---|---|
| P1 | `simulator/` → `backend/` | 8100 → 8000 | Telemetry generation, ingestion, storage (SQLite) + live WS |
| P2 | `ml/` | 8200 | LightGBM predictive fault engine (precursor probability + time-to-impact) |
| P3 | `rca/` | 8300 | Topology-aware cascade root-cause correlation |
| P4 | `copilot/` | 8400 | Offline LLM copilot (Ollama + runbook RAG) |
| P5 | `twin/` | — | Digital-twin generator (Containerlab + FRR configs) |
| P6 | `frontend/` | 8080 | Self-contained operator dashboard |

## Topology

Six nodes, defined once in [`data/topology.json`](data/topology.json) and loaded by both the simulator and backend:

| Node | Role | Connects to |
|---|---|---|
| `ce-site-a` | CE (Site A) | `pe-east` |
| `ce-site-b` | CE (Site B) | `pe-west` |
| `pe-east` | PE | `ce-site-a`, `p-core-1`, `p-core-2` |
| `pe-west` | PE | `ce-site-b`, `p-core-1`, `p-core-2` |
| `p-core-1` | P core | full mesh with PEs + `p-core-2` |
| `p-core-2` | P core | full mesh with PEs + `p-core-1` |

Plus 2 IPsec tunnels (Site A ⇄ Site B) and 3 BGP sessions (2 eBGP CE–PE, 1 iBGP VPNv4 PE–PE).

## Quick start

Three ways to run the stack, from most turnkey to most flexible.

### Option A — Docker (one command)

```bash
docker compose up --build
```

> **Air-gap note:** the image build needs PyPI once. On the offline network, build the images on a connected machine, `docker save`/`docker load` them, then `docker compose up` works with zero connectivity.

> **`ml/models/` prerequisite:** the `ml` container will crash-loop on a fresh clone / first `docker compose up` — `ml/models/` is git-ignored and must be populated before the service can start. From the repo root, with the backend + simulator up, run once:
> ```bash
> python -m ml.dataset.generate   # ~30 min, generates labelled training data
> python -m ml.train              # trains + saves models to ml/models/
> ```
> Only then will `ml`'s `Predictor` find `classifier.txt`/`regressor.txt`/`features.json` instead of raising `FileNotFoundError` at startup. See `ml/README.md` for details.

### Option B — Plain Python (recommended for dev)

Requires **Python 3.11+**. Two terminals, **both from the repo root**:

```bash
# one-time setup
python -m venv .venv
.venv\Scripts\activate            # Windows   (Linux/mac: source .venv/bin/activate)
pip install -r backend/requirements.txt -r simulator/requirements.txt
```

```bash
# Terminal 1 — backend
uvicorn app.main:app --app-dir backend --port 8000
```

```bash
# Terminal 2 — simulator
set PYTHONPATH=simulator          # Windows   (Linux/mac: export PYTHONPATH=simulator)
python -m sim.main
```

Config lives in `.env` (copy `.env.example`); defaults work out of the box.

### Option C — All-in-one single container

For a portable demo / air-gapped box: one image runs all five services **and** the dashboard, launched by a tiny stdlib process supervisor (`docker/supervisor.py`). Everything talks over `localhost` inside the container, so there is no inter-service config.

```bash
# build from the repo root (bakes in ml/models if present)
docker build -f docker/Dockerfile.allinone -t drishti-allinone .

docker run --rm \
  -p 8080:8080 -p 8000:8000 -p 8100:8100 -p 8200:8200 -p 8300:8300 -p 8400:8400 \
  --add-host=host.docker.internal:host-gateway \
  drishti-allinone
```

Then open the dashboard at **http://localhost:8080**.

- The supervisor waits for the backend to be healthy before starting dependents, and streams each service's logs with a `[name]` prefix.
- Unlike the Compose `ml` service, a missing `ml/models/` here only disables predictions (ml is non-critical) — the container stays up and the rest of the pipeline still works.
- Ollama still runs on the host (reached via `host.docker.internal`); the copilot degrades to a templated narrative if it is absent.

> **Air-gap:** build once on a connected machine, then `docker save drishti-allinone | gzip > drishti.tar.gz`, copy it over, and `docker load < drishti.tar.gz` — a single artifact, zero connectivity.

### Verifying it's alive

```bash
curl localhost:8000/health
curl localhost:8000/topology
curl "localhost:8000/metrics/pe-east?minutes=5"
curl "localhost:8000/events?minutes=60"
```

Interactive API docs: **http://localhost:8000/docs** (backend) and **http://localhost:8100/docs** (simulator/fault API).

## Running a demo

Three levels of demo, from zero-setup to full-stack post-deploy check.

### End-to-end demo (no services required)

`scripts/pipeline_demo.py` runs the whole pipeline in one process on a built-in golden-path cascade — simulator events → ml precursor predictions → rca correlation (root cause + cascade) → copilot narrative — using the real topology, runbooks, and correlation code. It needs nothing running: if a local Ollama answers it writes the LLM narrative, otherwise the copilot degrades to its deterministic templated summary, so it always produces output on an air-gapped box.

```bash
python -m scripts.pipeline_demo          # human-readable, stage by stage
python -m scripts.pipeline_demo --json   # + full result as JSON
COPILOT_MODEL=qwen3:8b python -m scripts.pipeline_demo   # pick a pulled model
```

`tests/test_e2e_pipeline.py` asserts this same chain — the one test that exercises more than one service together.

### Live-stack smoke test (services must be up)

Where the demo above is in-process, `scripts/smoke.py` drives the real HTTP wiring against a **running** stack: it health-checks every service, injects a fast-ramping fault at the simulator, waits for rca to correlate an incident from the resulting `simulator → backend → rca` event stream, then has copilot explain that incident *by id* (forcing the copilot → rca fetch). It prints PASS/FAIL per step and exits non-zero on failure, so it also works as a post-deploy check.

```bash
python -m scripts.smoke                 # localhost defaults, ml/frontend optional
python -m scripts.smoke --timeout 120   # allow longer for the fault to ramp
```

### Edge-case simulations you can run

The dashboard has a **Fault injection** panel (pick scenario + node → Inject), and `scripts/scenarios.py` provides a catalog of named, one-command scenarios — including adversarial edge cases — to exercise the whole pipeline against a live stack:

```bash
python -m scripts.scenarios list
python -m scripts.scenarios run dual-independent --wait 25   # 2 distant faults -> must stay 2 incidents
python -m scripts.scenarios run cascade-core-then-edge       # staggered core -> edge cascade
python -m scripts.scenarios run resolve --wait 20            # incident open -> auto-resolve lifecycle
python -m scripts.scenarios clear                            # flush faults + reset rca
```

`--wait N` polls rca and prints the resulting incidents, so a scenario doubles as an integration check.

| Scenario | What it stresses | Expected |
|---|---|---|
| `dual-independent` | topological clustering | ce-site-a and ce-site-b are 4 hops apart → **two separate incidents**, not one merged |
| `dual-core` | adjacent multi-symptom | both cores (adjacent) → **one** incident spanning them |
| `cascade-core-then-edge` | precursor → impact ordering | core degrades first → rooted at the core, edge folded into the cascade |
| `storm` | multi-symptom correlation | network-wide faults, all within `cascade_max_hops` of the core → one large incident |
| `resolve` | open → resolve lifecycle | incident appears, then auto-resolves as symptoms decay (or `clear` to flush instantly) |

## Injecting faults (precursor patterns for ML)

Faults ramp **gradually** (`ramp_seconds`), hold at full effect (`hold_seconds`), then auto-expire. The ramp phase is the precursor signature the Phase-2 models learn to detect. Events emitted during a fault carry `details.fault_id`/`details.scenario` — ground-truth labels for free.

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
| `congestion_ramp` | Utilization climbs (default +45%); latency/jitter/loss follow once the link runs hot | High-util warning at 50%, queue-drop error at 90% of ramp |
| `bgp_flap_precursor` | Jitter creep + slight loss | Keepalive-delay/hold-timer warnings at accelerating cadence, then a down/up flap burst |
| `link_degradation` | Packet loss ramps to `max_loss_pct`, jitter ×3 | CRC/input-error syslogs, accelerating |

## API reference

### Backend (`:8000`)

| Endpoint | Description |
|---|---|
| `POST /telemetry/ingest` | Simulator pushes a batch every 5s (validated, stored in SQLite) |
| `GET /topology` | Node/link/tunnel/BGP graph, with per-node interface list |
| `GET /metrics/{node_id}?minutes=15&interface=&limit=` | Recent interface + tunnel timeseries for a node |
| `GET /events?minutes=60&node_id=&event_type=&severity=&limit=` | Recent events, filterable |
| `WS /ws/live` | Every accepted batch pushed as JSON (for the dashboard) |
| `GET /health` | Liveness |

### Simulator / fault injection (`:8100`)

| Endpoint | Description |
|---|---|
| `GET /scenarios` | Available fault scenarios + default params |
| `POST /faults` | Inject a fault (see [Injecting faults](#injecting-faults-precursor-patterns-for-ml)) |
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
| `POST /admin/reset` | Flush the event buffer + incidents (clean-slate reset for demos, so incidents don't linger through symptom decay) |
| `GET /health` | Liveness |


<img width="775" height="596" alt="Drishti Metrics 0826 demo" src="https://github.com/user-attachments/assets/eb396929-6f01-49a9-bbaf-f93ef2032e4a" />

Metrics as of 08-08-2026.

### copilot / RCA explainer (`:8400`)

| Endpoint | Description |
|---|---|
| `POST /explain` | Body `{"incident_id": "..."}` (fetched from rca) or `{"incident": {...}}`. Returns a grounded root-cause narrative + the runbooks it used. Needs a local Ollama server + a pulled chat model; degrades to a templated summary (HTTP 200, `llm_available:false`) if Ollama is down |
| `GET /health` | Liveness |

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
scripts/     pipeline_demo.py — offline in-process golden-path demo; smoke.py —
             live-stack HTTP smoke test / post-deploy check; scenarios.py —
             named edge-case scenario runner
docker/      Dockerfile.allinone + supervisor.py — single-container image running
             all services (Option C); requirements-allinone.txt is their union
tests/       test_e2e_pipeline.py — cross-service integration test (the rest live
             per-service under <svc>/tests/)
data/        topology.json (tracked) + runbooks/ (tracked, operator runbooks the
             copilot retrieves over) + drishti.db (generated, git-ignored)
```

## Notes for teammates

- **Predictive fault engine** (`ml/`, Phase 2): LightGBM classifier + regressor, standalone FastAPI service (:8200), runs offline on buffered metrics. See `ml/README.md` to generate training data via fault injection and train models.
- **Cascade RCA** (`rca/`, Phase 3): topology-aware root-cause + cascade engine, standalone FastAPI service (:8300). Correlates events (+ optional Phase 2 predictions) into incidents with a ranked root cause and predicted blast radius; deterministic pure-Python graph heuristics, no extra ML deps. See `rca/README.md`.
- **Offline copilot** (`copilot/`, Phase 4): explains an rca incident as an operator narrative via a local Ollama LLM + TF-IDF retrieval over `data/runbooks/` (:8400), fully offline. Model-agnostic (`COPILOT_MODEL`); needs a local Ollama server. See `copilot/README.md`.
- **Digital twin** (`twin/`, Phase 5): generates a Containerlab lab + FRR configs from `data/topology.json` (`python -m twin.generate`) to stand the network up as real routers for fix validation. Generation is offline + tested; live `containerlab deploy` needs the containerlab toolchain. See `twin/README.md`.
- **Network sim**: scenarios live in `simulator/sim/faults.py` — add a new scenario by adding a `SCENARIOS` entry, a branch in `modifiers_for`, and a `_tick_<name>` event emitter.
- **Dashboard** (`frontend/`, Phase 6): self-contained static page (`:8080`, no build) consuming all four services + the `/ws/live` feed; CORS is open on every service.
- **Storage**: plain SQLite in WAL mode, no ORM — schema in `backend/app/repository/db.py`.

