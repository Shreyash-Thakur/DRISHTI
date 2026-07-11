# ml/ — Predictive Fault Engine (Phase 2, placeholder)

This package will hold the LSTM/LightGBM models that forecast precursor
anomalies and time-to-impact.

## Getting training data

Two options, pick whichever fits your workflow:

**1. Read the SQLite DB directly** (simplest for offline experimentation):

```python
import sqlite3
import pandas as pd

conn = sqlite3.connect("data/drishti.db")
df = pd.read_sql_query(
    "SELECT * FROM interface_metrics WHERE node_id = 'pe-east' ORDER BY ts",
    conn,
)
events = pd.read_sql_query("SELECT * FROM events ORDER BY ts", conn)
```

Tables: `interface_metrics`, `tunnel_metrics`, `events` — see
`backend/app/repository/db.py` for the exact schema.

**2. Import the backend services directly** (no HTTP round-trip):

```python
# with backend/ on your PYTHONPATH
from pathlib import Path
from app.services import telemetry_service

resp = telemetry_service.get_node_metrics(
    Path("data/drishti.db"), Path("data/topology.json"),
    "pe-east", minutes=60, interface=None, limit=5000,
)
```

## Generating labelled precursor data

Inject a fault, let it ramp, and you have a labelled anomaly window:

```bash
curl -X POST localhost:8100/faults -H "Content-Type: application/json" \
  -d '{"scenario": "congestion_ramp", "node_id": "pe-east"}'
```

The `events` table rows carry `details.fault_id` / `details.scenario` in their
JSON `details` column — use those as ground-truth labels.
