# Phase 6 Operator Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a self-contained, offline, no-build operator dashboard (`frontend/index.html`) that ties together backend (:8000), ml (:8200), rca (:8300), and copilot (:8400) into one NOC screen, per `docs/superpowers/specs/2026-07-14-phase6-operator-dashboard-design.md`.

**Architecture:** One HTML file — inline CSS design system + inline vanilla ES-module JS. Each panel fetches its service independently on a 5s poll (+ backend `/ws/live` for the event ticker) and degrades alone if its service is down. Service URLs default to localhost and are overridable via query params. Prerequisite: add permissive CORS to the three services that lack it.

**Tech Stack:** HTML/CSS/vanilla JS (no framework, no bundler, no CDN). Python only for the tiny offline contract test + the compose static-server. `node --check` for JS syntax validation.

## Global Constraints

- Zero build, zero `npm install`, zero external requests except to local DRISHTI services (hard air-gap).
- No web fonts / CDN / chart libs — system font stack, inline SVG, hand-written meters.
- Read-only: the dashboard observes + requests explanations; it never mutates the network.
- Defensive rendering everywhere (optional chaining / defaults); one service down must not blank the screen.
- Dashboard served on `:8080` (static). Query-param overrides: `?backend=&ml=&rca=&copilot=`.

---

## Task 1: CORS on ml / rca / copilot services

**Files:** Modify `ml/service/app.py`, `rca/service/app.py`, `copilot/service/app.py`.

The backend already has `CORSMiddleware(allow_origins=["*"])`; the dashboard's cross-origin fetches to the other three fail without the same. Add to each `create_app()` (after `app = FastAPI(...)`, before/after `include_router`):

```python
from fastapi.middleware.cors import CORSMiddleware
...
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )
```

- [ ] **Step 1:** Add the import + middleware to `ml/service/app.py`'s `create_app`.
- [ ] **Step 2:** Same for `rca/service/app.py`.
- [ ] **Step 3:** Same for `copilot/service/app.py`.
- [ ] **Step 4:** Sanity — `python -c "from ml.service.app import app; from rca.service.app import app as r; from copilot.service.app import app as c; print('apps import ok')"` (imports must still succeed; lifespans aren't triggered on import).
- [ ] **Step 5: Run the affected suites** — `python -m pytest ml/tests rca/tests copilot/tests -q` → still all green (CORS middleware doesn't affect existing tests).
- [ ] **Step 6: Commit** — `git add ml/service/app.py rca/service/app.py copilot/service/app.py && git commit -m "services: enable permissive CORS on ml/rca/copilot for the dashboard"`

---

## Task 2: Offline contract guard test

**Files:** Create `frontend/tests/__init__.py`, `frontend/pytest.ini`, `frontend/tests/test_contract.py`.

The dashboard JS can't run in CI here, so guard the *data contracts* it depends on: assert the service response builders still emit the keys the dashboard reads. This catches backend drift that would silently break the frontend.

- [ ] **Step 1: `frontend/pytest.ini`** — `[pytest]\ntestpaths = tests`; empty `frontend/tests/__init__.py`.
- [ ] **Step 2: `frontend/tests/test_contract.py`**

```python
"""Guards the response shapes frontend/index.html depends on. If a service changes
its output keys, this fails so the (CI-untestable) dashboard doesn't silently break."""
from datetime import datetime, timezone

from rca.config import Settings as RcaSettings
from rca.correlate import correlate
from rca.graph import Graph
from rca.symptoms import Symptom

BASE = datetime(2026, 7, 14, tzinfo=timezone.utc)


def test_incident_shape_the_dashboard_reads():
    graph = Graph.from_path("data/topology.json")
    sym = Symptom("node", "p-core-1", "p-core-1", BASE, BASE, "error", 5.0, 1)
    incident = correlate([sym], graph, RcaSettings())[0].to_dict()
    # keys the incidents panel + Explain button rely on
    assert {"incident_id", "severity", "root_cause", "symptoms", "cascade"} <= incident.keys()
    assert {"node_id", "confidence", "rationale"} <= incident["root_cause"].keys()
    for entry in incident["cascade"]:
        assert {"anchor_type", "anchor_id", "hops_from_root"} <= entry.keys()


def test_explain_output_shape_the_dashboard_reads():
    import anyio

    from copilot.explain import explain
    from copilot.llm import ChatResult
    from copilot.rag import Snippet

    class _R:
        def retrieve(self, q, k):
            return [Snippet("link-degradation", "Symptoms", "CRC", 0.9)]

    class _C:
        model = "m"
        async def chat(self, s, u):
            return ChatResult(content="Summary: x", model="m", available=True)

    incident = {"incident_id": "i", "root_cause": {"node_id": "p-core-1"},
                "symptoms": [], "cascade": []}
    out = anyio.run(explain, incident, _R(), _C(), 3)
    assert {"narrative", "llm_available", "retrieved_runbooks", "root_cause_node"} <= out.keys()
```

- [ ] **Step 3: Run** — `python -m pytest frontend/tests -q` → `2 passed`.
- [ ] **Step 4: Commit** — `git add frontend/pytest.ini frontend/tests && git commit -m "frontend: add offline data-contract guard test"`

---

## Task 3: The dashboard (`frontend/index.html`)

**Files:** Create `frontend/index.html` (single self-contained file).

Because the whole deliverable is one HTML file, write it in full (do not stub). It MUST contain, all inline:

**(a) `<style>` — design system.** Read the dataviz skill's palette guidance first (invoke `dataviz`). Define CSS custom properties on `:root` for a dark NOC theme + a light override via `@media (prefers-color-scheme: light)`. Include a severity scale: `--ok`, `--info`, `--warning`, `--error`, `--critical` (colorblind-safe, sufficient contrast in both themes). System font stack. A responsive CSS grid: header row, then a 12-col grid collapsing to 1 col under ~900px. Style: health dots, topology SVG, stat tiles with a probability meter (a `<div>` bar, width = probability), incident cards with a severity chip, a scrolling event ticker, and the copilot narrative panel (preserve whitespace with `white-space: pre-wrap`).

**(b) `<body>` markup.** Header (`DRISHTI — NOC` + a health-dot per service). A `<main>` grid with sections: `#topology`, `#predictions`, `#incidents`, `#copilot`, `#events`. Each has a title and a content container the JS fills. A small config line showing the resolved service URLs.

**(c) `<script type="module">` — logic.** Implement exactly these, using `fetch`/WebSocket, no libraries:

- URL config: read `?backend=&ml=&rca=&copilot=` (defaults `http://localhost:800{0}`/`8200`/`8300`/`8400`), expose as `SVC`.
- `safeJson(url, opts)` — fetch with an `AbortController` timeout (~4s); returns `null` on any failure (never throws).
- `pollHealth()` — GET each `${svc}/health`; set each dot class ok/down.
- `loadTopology()` (once) — GET `${SVC.backend}/topology`; render an inline SVG with the 6 nodes at FIXED positions (`ce-site-a`,`ce-site-b` left; `pe-east`,`pe-west` middle; `p-core-1`,`p-core-2` right) and lines for each link. Keep node id→(x,y) as a constant map.
- `refreshPredictions()` — GET `${SVC.ml}/predictions`; render tiles sorted by `precursor_probability` desc; meter width = `probability*100%`; show `estimated_seconds_to_impact` (`—` when null); "ml offline" if null response.
- `refreshIncidents()` — GET `${SVC.rca}/incidents`; render a card per incident (severity chip, `root_cause.node_id` + `confidence` + `rationale[]`, ordered `cascade` with `+{hops}h` and `at_risk_reason`); each card has an **Explain** button carrying the incident object. Tint the matching topology nodes (root cause = ring; cascade nodes = warning tint).
- `explainIncident(incident)` — POST `${SVC.copilot}/explain` `{incident}`; render `narrative` (pre-wrap) + `model`/`llm_available` + `retrieved_runbooks` in `#copilot`. On failure show a clear message.
- `connectEvents()` — open `new WebSocket(SVC.backend.replace('http','ws')+'/ws/live')`; on `telemetry` messages prepend each event to the ticker (cap ~50), color by severity; on close, reconnect after a backoff and meanwhile `refreshEventsByPoll()` (GET `/events?minutes=5`).
- Kick off: `loadTopology()`, then `pollHealth`/`refreshPredictions`/`refreshIncidents` immediately and every 5s (`setInterval`), and `connectEvents()`.

- [ ] **Step 1:** Invoke the `dataviz` skill; adopt its palette/contrast guidance for the severity scale + tiles.
- [ ] **Step 2:** Write `frontend/index.html` in full per (a)/(b)/(c).
- [ ] **Step 3: Static checks** — extract the module script and `node --check` it (syntax); confirm the HTML parses (Python `html.parser`). Fix any errors.
- [ ] **Step 4: Commit** — `git add frontend/index.html && git commit -m "frontend: add self-contained offline operator dashboard"`

---

## Task 4: Compose, docs, manual verification

**Files:** Modify `docker-compose.yml`, `frontend/README.md`, root `README.md`.

- [ ] **Step 1: `docker-compose.yml`** — add a `frontend` service: `image: python:3.11-slim`, `working_dir: /site`, `volumes: ["./frontend:/site"]`, `command: python -m http.server 8080`, `ports: ["8080:8080"]`, `depends_on: [backend]`. (No build; just serves the static file.)
- [ ] **Step 2: Rewrite `frontend/README.md`** — how to serve (`cd frontend && python -m http.server 8080`, open `http://localhost:8080`), the `?backend=&ml=&rca=&copilot=` overrides, the offline/no-build + CORS rationale, and what each panel shows. Link the design spec.
- [ ] **Step 3: Root `README.md`** — check off roadmap item 6 (`6. ✅ Operator dashboard (self-contained static, no build)`); note all 6 phases complete; add the `frontend/` layout + teammates bullet; update the architecture diagram's frontend box to "(working)".
- [ ] **Step 4: Manual e2e** — serve `frontend/`, start the full stack (+ Ollama), inject a fault, and confirm: health dots green; a prediction tile climbs; an incident card shows root cause + cascade; **Explain** returns a narrative; the event ticker streams. (Serve + load + endpoint-wiring is verified here; full visual QA needs a browser.)
- [ ] **Step 5: Commit** — `git add docker-compose.yml frontend/README.md README.md && git commit -m "frontend: wire compose static-server, docs; Phase 6 dashboard"`

---

## Self-Review Notes

- **Spec coverage:** CORS prerequisite (Task 1), contract guard (Task 2), the self-contained dashboard with all six panels + query-param config + independent degradation + WS ticker (Task 3), compose/docs/verify (Task 4). All spec sections mapped.
- **No placeholders:** the small code (CORS snippet, contract test) is written out; the dashboard is a single deliverable written in full in Task 3 (not stubbed).
- **Contract consistency:** the panels read exactly the keys Phases 1–4 emit (`precursor_probability`/`estimated_seconds_to_impact`; `incident.root_cause.{node_id,confidence,rationale}` + `cascade[].{anchor_type,anchor_id,hops_from_root,at_risk_reason}`; explain `{narrative,llm_available,retrieved_runbooks}`), and Task 2's test guards those.
- **Honest scope:** JS runtime/visual behavior can't be auto-tested here; verification = data-contract test + `node --check` + serve/load/wiring. Called out, not overclaimed.
