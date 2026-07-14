# Phase 6 — Operator Dashboard (design)

Status: proposed (authored autonomously under a "continue implementation, do not
stop" directive; see process note at end)
Date: 2026-07-14

## Purpose

DRISHTI's roadmap Phase 6 is the **operator dashboard** — the single screen a NOC
operator watches, tying together all four backend services: live telemetry
(:8000), predictive risk (:8200), correlated incidents + cascade (:8300), and the
LLM explanation (:8400). It turns the whole pipeline into something a human can act
on: *what's happening, what's about to break, where the root cause is, and what to
do.*

## Deviation from roadmap (offline-forced)

The roadmap says "React dashboard". A React app means a bundler (`vite`/CRA) and
`npm install` from the public registry — an outbound dependency fetch that breaks
the project's hard air-gap constraint, and isn't reliably runnable in the offline
environment. So Phase 6 is a **single self-contained static file**
(`frontend/index.html`): inline CSS + vanilla ES-module JavaScript, **zero build
step, zero CDN, zero external requests** except to the local DRISHTI services. It
opens straight from disk or from any static file server, on the closed network,
with no toolchain. This is the same kind of documented, offline-forced deviation as
Phase 4 (Mistral→any Ollama model, ChromaDB→TF-IDF) and Phase 5 (generator-only).
The component structure inside the file is kept modular so it could be ported to
React later without re-architecting.

## Non-goals

- No build tooling, transpilation, bundler, or `node_modules`.
- No external assets — no web fonts, icon packs, chart libraries, or analytics.
- No authentication (closed network, single-operator hackathon scope).
- No write actions to the network — the dashboard is read-only observation plus
  requesting an LLM explanation. Remediation stays a human decision (Phase 5 twin
  validates fixes).

## Prerequisite change: CORS on the other services

The backend (:8000) already sets permissive CORS. The dashboard is served from a
different origin (a static server / `file://`), so it makes cross-origin requests
to **all four** services. The `ml` (:8200), `rca` (:8300), and `copilot` (:8400)
FastAPI apps must get the same `CORSMiddleware(allow_origins=["*"])` the backend
has. This is a small, consistent addition to each app and is part of this phase.

## Architecture

`frontend/index.html` — one file, three inline parts:

1. **CSS** — a compact design system: CSS custom properties for a dark NOC theme
   (light theme via `prefers-color-scheme`), a severity color scale
   (ok/info/warning/error/critical), responsive CSS grid layout. No external fonts
   (system font stack).
2. **Markup** — a header (title + per-service health dots), then a responsive grid
   of panels.
3. **JS (ES modules, inline)** — a small set of functions per panel: `fetch` +
   render on a 5-second poll (aligned to the simulator's tick), plus a backend
   `/ws/live` WebSocket for the live event ticker. Each panel fetches and renders
   **independently**, so one service being down degrades only its own panel.

Service base URLs default to `localhost:{8000,8200,8300,8400}` and are overridable
via URL query params (`?backend=…&ml=…&rca=…&copilot=…`) so the dashboard works
against remote hosts on the closed network without editing the file.

### Panels

- **Service health strip** — a dot per service from each `GET /health` (green =
  ok, red = unreachable). Immediate "is the stack up" read.
- **Topology map** — inline SVG with the 6 nodes at fixed positions (CE left, PE
  middle, P-core right) and the 7 links drawn from `GET :8000/topology`. Each node
  is tinted by its current worst state (from predictions + incidents); links on a
  cascade path are highlighted. Nodes that are an incident root cause get a ring.
- **Predictions** — stat tiles from `GET :8200/predictions`: node/interface,
  precursor probability (as a meter), estimated time-to-impact. Sorted by
  probability descending. "ml offline" note if unreachable.
- **Incidents** — a card per `GET :8300/incidents`: severity chip, root-cause node
  + confidence + rationale, and the cascade list (ordered, with hop counts). An
  **Explain** button per card.
- **Copilot** — clicking **Explain** POSTs the incident to `:8400/explain` and
  renders the returned narrative (Summary / root cause / blast radius / checks) and
  which runbooks it used; shows the `llm_available:false` templated fallback plainly
  when Ollama is down.
- **Live events ticker** — a scrolling list fed by the backend `/ws/live`
  WebSocket (falls back to polling `GET :8000/events` if the socket drops),
  color-coded by severity.

## Data contracts (already fixed by Phases 1–4)

- `:8000/topology` → `{nodes:[{id,role,…}], links:[{id,a,b,kind,…}], tunnels, bgp_sessions}`
- `:8000/events?minutes=` → `{count, events:[{ts,node_id,severity,event_type,message,details}]}`
- `:8200/predictions` → `[{node_id,interface,precursor_probability,estimated_seconds_to_impact}]`
- `:8300/incidents` → `[{incident_id,severity,root_cause{node_id,confidence,rationale},symptoms,cascade}]`
- `:8400/explain` (POST `{incident}`) → `{narrative,llm_available,retrieved_runbooks,…}`

The dashboard reads these shapes directly; no adapter layer.

## Error handling / resilience

- Every fetch is wrapped: a failed/timed-out request marks that panel "unavailable"
  and the poll loop continues (no unhandled promise rejections, no blank screen).
- The WS reconnects with a capped backoff; while disconnected the ticker falls back
  to polling `/events`.
- All rendering is defensive against missing fields (optional chaining / defaults),
  since a service may return partial data during warm-up.

## Testing / verification

- **Offline data-shape guard** (the one automatable unit test): a tiny pytest
  (`frontend/tests/test_contract.py`) asserting the endpoint response shapes the
  dashboard depends on still match what the services actually produce — importing
  the rca/copilot/… response builders and checking the keys the JS reads exist.
  This catches a backend contract drift breaking the untestable-in-CI frontend.
- **Static checks:** `node --check` on the extracted script (syntax), and an HTML
  well-formedness parse.
- **Manual e2e:** serve `frontend/` (`python -m http.server 8080`), open it with
  the stack running, inject a fault, and watch predictions rise, an incident card
  appear with root cause + cascade, and **Explain** return a narrative. (Full
  visual/interaction verification needs a browser; structural + endpoint-wiring
  verification is done here.)

## Repo/docs updates

- `frontend/README.md` rewritten: how to serve it, the query-param overrides, the
  offline/no-build rationale, the CORS note.
- Root `README.md`: check off roadmap item 6; note the dashboard is a static file;
  teammates/layout bullet; mark the project's 6-phase roadmap complete.
- `docker-compose.yml`: add a `frontend` service serving the static file via
  `python -m http.server` (no build), port `8080`. (Optional but keeps the
  one-command-up story.)
- CORS middleware added to `ml`/`rca`/`copilot` apps (see above).

## Process note

Authored autonomously under a standing "continue implementation, do not stop"
instruction. The React→vanilla-single-file deviation is forced by the air-gap (no
registry install) and documented above; the internal structure stays
component-shaped for a later React port. Full interactive/visual QA requires a
browser and is the one thing that can't be self-verified in this environment —
called out honestly rather than claimed.
