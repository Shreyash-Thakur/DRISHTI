# Phase 4 — Offline LLM Copilot (design)

Status: proposed (authored autonomously under a "continue implementation, do not
stop" directive; see process note at end)
Date: 2026-07-14

## Purpose

DRISHTI's roadmap Phase 4 adds the **operator-facing explanation layer**. Phases
1–3 detect, predict, and localize; none of them *explain*. Phase 4 takes a
correlated incident from the Phase 3 RCA engine (`:8300`) and produces a concise,
operator-readable root-cause narrative + recommended next steps, grounded in local
runbooks — **fully offline**, using a local Ollama LLM. This is the "so what do I
do about it" layer the NOC actually reads.

`copilot/README.md` already fixes the contract: *"`explain.py` takes a correlated
incident (from the Phase-3 graph engine) and produces an operator-readable
root-cause narrative."*

## Non-goals

- **No cloud LLM, no outbound calls.** Generation runs against a local Ollama
  server (`http://localhost:11434`). Zero telemetry, zero model downloads at
  runtime. This is a hard constraint, identical to Phases 1–3.
- **No fine-tuning / no training.** The copilot is inference + retrieval only.
- **No conversational/chat memory (this phase).** One incident in → one
  explanation out. A multi-turn "ask the copilot" mode can come later.
- **No agentic actions.** The copilot explains and recommends; it never executes
  remediation (Phase 5's digital twin validates fixes).
- **No embedding-based / vector RAG in this phase.** See "Retrieval" below — the
  target environment's Ollama server has embeddings disabled, and ChromaDB's
  default embedder downloads from HuggingFace (violating offline). Lexical
  retrieval is used instead; semantic retrieval is a documented future swap-in.

## Model choice

The roadmap names "Mistral 7B", but the copilot is **model-agnostic**: the model is
a setting (`COPILOT_MODEL`), defaulting to `mistral:7b` (roadmap intent) and
overridable to whatever the local Ollama server actually has pulled (e.g.
`qwen3:8b` in the current dev environment — the only model present offline). The
client works against any Ollama chat-capable model. Thinking-style models are
handled by requesting `think: false` so the response is the narrative, not a
reasoning trace.

## Architecture

A new standalone package `copilot/` with its own FastAPI service (`:8400`),
continuing the port convention (backend `:8000` → simulator `:8100` → ml `:8200` →
rca `:8300` → copilot `:8400`) and the `pydantic-settings` prefix convention
(`COPILOT_`):

```
copilot/
  config.py     Settings (COPILOT_ prefix): port, ollama_url, model, rca_url,
                 runbooks_dir, num_predict, num_ctx, top_k
  llm.py        thin Ollama /api/chat client (offline, model-agnostic, graceful
                 degradation when the server/model is unavailable)
  rag.py        lexical (TF-IDF cosine) retrieval over data/runbooks/*.md —
                 pure-Python, no embeddings, no extra deps
  prompt.py     turns an rca incident (+ retrieved runbook snippets) into the
                 system+user chat messages
  explain.py    orchestration: incident dict -> retrieve -> prompt -> llm -> result
  service/
    routes.py    POST /explain, GET /health
    app.py       FastAPI app + lifespan (builds the retriever, holds the client)
  main.py       uvicorn entrypoint (python -m copilot.main)
  tests/         pytest suite (mirrors rca/tests layout; LLM is faked in unit tests)
  Dockerfile
  README.md     (rewritten from the current placeholder)

data/runbooks/  NEW tracked corpus — one markdown runbook per fault scenario
                 (congestion, bgp flap, link degradation) + a general RCA runbook
```

The service is **independently usable**: `POST /explain` accepts a full incident
payload directly (no dependency on `:8300` being up), and *optionally* accepts just
`{"incident_id": "..."}`, in which case it fetches the incident from the rca
service. If Ollama is unreachable, `/explain` returns a structured degraded
response (retrieved runbooks + a templated fallback summary + an `llm_available:
false` flag) rather than a 500 — the operator still gets the RCA facts and runbook
pointers.

## LLM client (`copilot/llm.py`)

- `OllamaClient(base_url, model, num_predict, num_ctx)` with
  `async chat(system: str, user: str) -> ChatResult` where
  `ChatResult = {content: str, model: str, available: bool}`.
- POSTs `/api/chat` with `stream: false`, `think: false`,
  `options: {num_predict, num_ctx, temperature: 0.2}` (low temperature — this is
  explanation, not creativity). Short connect timeout, generous read timeout
  (local 7–8B generation can take tens of seconds).
- Any transport/HTTP error → `ChatResult(content="", available=False)`; never
  raises to the caller. The orchestration layer decides the degraded response.

## Retrieval (`copilot/rag.py`)

A tiny pure-Python **TF-IDF cosine retriever** over the local runbook corpus:

- `Retriever.from_dir(runbooks_dir)` loads every `*.md`, splits each into chunks
  (by markdown heading), tokenizes (lowercase, alphanumeric), and builds a TF-IDF
  matrix with numpy (already a dependency via ml; if numpy is undesired here, the
  cosine is small enough to hand-roll with dicts — decision: use numpy, it's
  installed and the code is clearer).
- `retrieve(query: str, top_k: int) -> list[Snippet]` where
  `Snippet = {runbook, heading, text, score}`, ranked by cosine similarity of the
  query's TF-IDF vector against each chunk. Ties broken by document order for
  determinism.
- The query is assembled from the incident's salient text: root-cause node + its
  role, the scenario labels on the symptoms, and representative event messages
  (which carry strong lexical signal — "CRC", "keepalive", "utilization", "BGP").

This is deterministic, needs no server, and is plenty for a handful of
scenario-keyed runbooks. **Documented future swap-in:** an embedding retriever
(Ollama `/api/embed` started with `--embeddings`, or ChromaDB with a pre-baked
local embedder) behind the same `Retriever` interface, for semantic recall over a
larger corpus.

## Runbook corpus (`data/runbooks/`)

New tracked markdown, one file per known failure mode, matched to the simulator's 3
scenarios plus a general playbook:

- `congestion.md` — sustained high utilization / output drops; checks + mitigations
  (QoS, capacity, reroute).
- `bgp-flap.md` — keepalive/hold-timer precursors, adjacency flaps; checks
  (timers, CPU, interface errors on the session path).
- `link-degradation.md` — CRC/input errors, failing optic/dirty fiber; checks
  (optical levels, cable/SFP swap, error counters).
- `rca-general.md` — how to read a cascade: root cause vs. blast radius, tunnels
  riding a core path, prioritizing by hops.

Each is short, operator-oriented, and written so the lexical retriever keys cleanly
off scenario keywords. Tracked in git (unlike models/data artifacts) — they're
source, and small.

## Prompt (`copilot/prompt.py`)

- **System message:** role ("You are DRISHTI, an offline NOC assistant for a secure
  MPLS/SD-WAN network"), the topology in one line, strict instructions: ground the
  answer in the provided RCA facts and runbook excerpts, be concise, use the
  operator's vocabulary, never invent nodes/metrics not present, output a fixed
  structure (Summary / Likely root cause / Blast radius / Recommended checks).
- **User message:** the incident rendered as compact facts (root cause anchor +
  confidence + rationale, symptoms with severities + sample messages, cascade with
  hops), followed by the retrieved runbook snippets with their headings.
- Pure function `build_messages(incident: dict, snippets: list[dict]) -> tuple[str, str]`
  (system, user) — fully unit-testable without the LLM.

## Orchestration (`copilot/explain.py`)

`async explain(incident: dict, retriever, client) -> dict` returning:

```
{
  incident_id, root_cause_node, model,
  llm_available: bool,
  narrative: str,              # LLM output, or a templated fallback if unavailable
  retrieved_runbooks: [{runbook, heading, score}],
}
```

Flow: build the retrieval query from the incident → `retriever.retrieve` →
`prompt.build_messages` → `client.chat`. If `client.chat` reports unavailable,
`narrative` is a deterministic template built from the RCA facts + top runbook
headings, and `llm_available` is `false`.

## Service (`copilot/service/`)

- `POST /explain` — body is either a full incident dict or `{"incident_id": "..."}`.
  For the id-only form, the service GETs `{rca_url}/incidents/{id}` (best-effort;
  404/unreachable → 502 with a clear message). Returns the `explain()` dict.
- `GET /health` — `{status: "ok", service: "drishti-copilot"}`.
- Lifespan builds the `Retriever` from `runbooks_dir` once at startup and holds one
  `httpx.AsyncClient` for the Ollama client + rca fetches.

## Error handling

- Ollama down / model missing / timeout → degraded (not fatal) `/explain` response
  with `llm_available: false`; `GET /health` stays `ok` (the service itself is up).
- rca unreachable for the id-only form → `502` with a message telling the caller to
  POST the incident directly.
- Empty/malformed incident → `422` (pydantic validation on a minimal shape:
  `incident_id`, `root_cause`, `symptoms`, `cascade`).
- Empty runbook corpus → retrieval returns `[]`; explanation still proceeds on RCA
  facts alone.

## Testing

- **`rag.py`** — a known query ("CRC input errors on the optic") ranks
  `link-degradation.md` first; unknown query returns low/empty; determinism.
- **`prompt.py`** — `build_messages` includes the root-cause node, each symptom's
  scenario, and the runbook headings; excludes nothing required.
- **`llm.py`** — against a mocked transport: success parses `message.content`;
  transport error yields `available=False` without raising.
- **`explain.py`** — with a fake retriever + fake client: LLM-available path returns
  the model narrative; unavailable path returns the templated fallback and
  `llm_available=false`.
- **service** — `TestClient`: `/health`; `/explain` with a full incident and a fake
  client on `app.state` returns 200 + expected shape; id-only form with rca stubbed.
- **Manual e2e** — backend+simulator+rca up, inject a fault, then
  `POST :8400/explain {"incident_id": "<from :8300>"}` and read the narrative;
  confirm it names the root-cause node and reflects the cascade. Also verified
  directly against the live Ollama server present in the dev environment.

## Repo/docs updates

- `copilot/README.md` rewritten from placeholder to describe the real package + the
  `:8400` API + the Ollama prerequisite (server running, a chat model pulled) +
  how the offline model choice works + running the tests.
- Root `README.md`: check off roadmap item 4, add the `:8400` API section, update
  the architecture diagram box and the `Notes for teammates`/repo-layout bullets.
- `docker-compose.yml`: add a `copilot` service (`:8400`). Ollama runs on the host,
  not in compose, so the container reaches it via
  `COPILOT_OLLAMA_URL=http://host.docker.internal:11434` (documented; on Linux the
  host-gateway mapping is noted). `depends_on` backend healthy; rca optional.

## Note on process

Authored autonomously under a standing "continue implementation, do not stop"
instruction, so the brainstorming approval gate was not run interactively. The two
notable, reversible deviations from the roadmap's literal wording — (1) lexical
retrieval instead of ChromaDB vector RAG, and (2) model-agnostic default instead of
hard-coded Mistral 7B — are both forced by the hard offline constraint in the
actual environment (embeddings disabled on the local Ollama server; only `qwen3:8b`
pulled) and are called out here and in `rag.py`/`config.py` for review. Both keep a
clean seam to adopt the roadmap's exact stack when a suitable offline embedder /
Mistral pull is available.
