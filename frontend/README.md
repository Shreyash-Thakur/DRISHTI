# frontend/ — Operator Dashboard (Phase 6)

A **single self-contained static file** (`index.html`) that ties the whole DRISHTI
stack into one NOC screen — live telemetry, predictive risk, correlated incidents +
cascade, and the LLM explanation. **No build step, no `npm`, no CDN, no external
requests** except to the local DRISHTI services. It opens straight from disk or any
static file server on the closed network.

See the design: [`docs/superpowers/specs/2026-07-14-phase6-operator-dashboard-design.md`](../docs/superpowers/specs/2026-07-14-phase6-operator-dashboard-design.md).

## Why not React?

A React app needs a bundler + `npm install` from the public registry — an outbound
dependency fetch that breaks DRISHTI's hard air-gap. So Phase 6 is vanilla
HTML/CSS/JS in one file. The internals are kept component-shaped so it could be
ported to React later without re-architecting.

## Run it

From the **repo root** (or `frontend/`):

```bash
cd frontend && python -m http.server 8080
# open http://localhost:8080
```

Or via `docker compose up` (the `frontend` service serves it on `:8080`).

The dashboard expects the other services running:

- backend `:8000` (telemetry, topology, events, `/ws/live`)
- ml `:8200` (predictions) — optional; panel shows "offline" if down
- rca `:8300` (incidents) — optional
- copilot `:8400` (explain) — optional; needs a local Ollama

Every panel fetches its service independently and degrades alone, so a missing
service never blanks the screen.

## Point it at other hosts

Service URLs are overridable via query params (no file edit):

```
http://localhost:8080/?backend=http://noc-host:8000&ml=http://noc-host:8200&rca=http://noc-host:8300&copilot=http://noc-host:8400
```

## Panels

- **Service health** — a dot per service (green up / red down) from each `/health`.
- **Topology** — the 6-node map; nodes tint by current worst state, incident root
  cause gets a ring, at-risk nodes tint amber.
- **Predicted risk** — stat tiles from `:8200`: precursor probability (meter) + ETA.
- **Incidents** — a card per `:8300` incident: severity, root cause + confidence +
  rationale, ordered cascade. **Explain** posts it to `:8400`.
- **Copilot explanation** — the LLM narrative (or the templated fallback when Ollama
  is down) + which runbooks it used.
- **Live events** — a ticker from the backend `/ws/live` socket (polls `/events` if
  the socket drops), color-coded by severity.

## CORS

The dashboard is a different origin from the services, so all four services set
permissive CORS (`allow_origins=["*"]`). Fine for the closed hackathon network.

## Tests

`frontend/tests/test_contract.py` guards the service response shapes the dashboard
reads (run from repo root: `pytest frontend/tests`). Live JS/visual behavior needs a
browser and isn't covered by automated tests.
