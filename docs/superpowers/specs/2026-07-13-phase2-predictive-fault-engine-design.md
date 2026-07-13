# Phase 2 — Predictive Fault Engine (design)

Status: approved
Date: 2026-07-13

## Purpose

DRISHTI's roadmap Phase 2 adds a predictive fault engine on top of the
Phase 1 simulator/backend. It must forecast **time-to-impact** for
in-progress fault precursors on any node/interface, fully offline, so
Phase 4 (LLM copilot) and Phase 6 (dashboard) have something to consume.

## Non-goals

- Fault-*type* classification (which scenario is happening) — out of
  scope for this phase; the roadmap's ML output is a time-to-impact
  estimate, not root-cause labeling (that's Phase 3's cascade RCA).
- LSTM — dropped in favor of LightGBM only, for hackathon time budget
  and easier debugging/retraining. Roadmap wording ("LSTM/LightGBM")
  is satisfied by LightGBM; LSTM can be revisited later if time permits.
- Live serving is in scope (not deferred to Phase 4) — the ml/ service
  runs standalone now.

## Architecture

`ml/` becomes a standalone package with its own FastAPI service
(`:8200`), mirroring the existing simulator/backend pattern:

```
ml/
  dataset/        data-gen harness — drives fault injection, pulls
                   labelled windows from data/drishti.db
  features.py      shared feature engineering (offline + online)
  train.py         trains classifier + regressor, saves to ml/models/
  service/         FastAPI app — WS client to backend, in-memory
                   rolling buffers, inference, GET/WS endpoints
  models/           trained model artifacts (git-ignored)
```

### Two models, not one

A single regressor trained only on ramp-phase data would extrapolate
nonsense when fed quiet/baseline telemetry (never having seen it). So:

1. **Precursor classifier** — binary, "is this node/interface currently
   showing a fault ramp pattern?" Trained with ramp-phase rows as
   positive and baseline rows as negative.
2. **Time-to-impact regressor** — trained only on ramp-phase rows,
   predicts seconds remaining until full effect.

At serving time: run the classifier first; only if its probability
clears a threshold do we run the regressor and surface a number.
Otherwise the node reports "no imminent risk" (`null`).

## Label definition (ground truth)

The simulator's fault ramp is deterministic and fully known to whoever
injects it: `progress = elapsed / ramp_seconds`, clamped to [0, 1].
"Impact" = the moment `progress` reaches 1.0 (fault enters its hold
phase at full effect). So for any telemetry sample timestamped during
an active fault's ramp:

```
seconds_to_impact = fault.started_at + ramp_seconds - sample.ts
```

This is exact, not heuristic — the data-gen harness controls
injection directly and records `started_at`/`ramp_seconds` per fault,
so every ramp-phase row gets a precise label for free. Baseline rows
(no active fault) are negative examples for the classifier and are
excluded from the regressor's training set.

## Data flow

1. **Data-gen harness** (`ml/dataset/generate.py`): assumes simulator
   (`:8100`) and backend (`:8000`) are already running. Repeatedly:
   - picks a scenario (`congestion_ramp`, `bgp_flap_precursor`,
     `link_degradation`), a target node/interface, and param variations
   - injects via `POST :8100/faults`, waits `ramp_seconds + hold_seconds`
   - clears the fault (`DELETE :8100/faults/{id}`) before the next run,
     so labels never overlap
   - records fault metadata (`fault_id`, `scenario`, `node_id`,
     `interface`, `started_at`, `ramp_seconds`) to a local manifest
   - after enough runs, reads `interface_metrics`/`tunnel_metrics`/
     `events` from `data/drishti.db` directly, joins against the
     manifest by `node_id` + timestamp range, computes labels, writes
     an engineered training set to `ml/dataset/training.parquet`
   - interleaves baseline-only gaps between runs so the classifier
     sees comparable quiet-period data

2. **`train.py`**: loads `training.parquet`, engineers rolling-window
   features (mean/std/min/max/slope over a few window sizes per
   node+interface, plus recent event counts/severities), trains the
   classifier and regressor, evaluates on held-out fault runs
   (leave-one-scenario-instance-out), saves both models + the feature
   list to `ml/models/`.

3. **Live service** (`ml/service/`): on startup, loads the trained
   models; fails fast if they're missing. Opens a WS client to
   backend's `/ws/live`, maintains an in-memory rolling buffer per
   node+interface, recomputes the same features on each incoming
   batch, runs classifier → (if above threshold) → regressor. Exposes:
   - `GET /predictions` — current state for every node/interface
   - `GET /predictions/{node_id}` — current state for one node
   - `WS /ws/predictions` — pushes updates as they're computed

   Response shape: `{node_id, interface, precursor_probability,
   estimated_seconds_to_impact}` (`estimated_seconds_to_impact` is
   `null` when `precursor_probability` is below threshold, or while a
   node's buffer is still warming up).

## Error handling

- Harness: retries/timeouts talking to the simulator's fault API;
  always clears faults between runs regardless of success/failure, so
  a failed run can't contaminate the next one's labels.
- Live service: WS reconnect with exponential backoff on backend
  disconnect; missing model files at startup is a fatal error, not a
  silent no-op; a node/interface with an insufficient buffer returns
  `null` rather than predicting on a partial window.

## Testing

- Unit tests for `features.py` (synthetic metric sequences → expected
  rolling stats).
- Unit tests for label computation (fault params → expected
  seconds-to-impact math, including edge cases at `progress` 0 and 1).
- Small end-to-end check: train on a tiny synthetic dataset, assert a
  model artifact is produced and `predict()` runs without error.
- Manual verification: run the harness briefly, train, start the
  service, inject a live fault via curl, and watch
  `precursor_probability` rise and `estimated_seconds_to_impact` fall
  before the scenario's critical event fires.

## Repo/docs updates

- `ml/README.md` gets rewritten to describe the real Phase 2 package
  (currently a placeholder).
- Root `README.md` roadmap entry for Phase 2 gets checked off once
  working, and the new `:8200` service gets an API reference section
  matching the existing backend/simulator ones.
