# frontend/ — Operator Dashboard (Phase 6, placeholder)

React dashboard goes here. The backend it will consume is already running:

- `GET http://localhost:8000/topology` — node/link graph for the topology map
- `GET http://localhost:8000/metrics/{node_id}?minutes=15` — timeseries for charts
- `GET http://localhost:8000/events?minutes=60` — event feed
- `WS  ws://localhost:8000/ws/live` — live telemetry push (every 5 s)

Interactive API docs: http://localhost:8000/docs

The WS feed sends one JSON message per ingested batch:

```json
{
  "type": "telemetry",
  "batch": {
    "source": "drishti-simulator",
    "sent_at": "…",
    "interface_metrics": [...],
    "tunnel_metrics": [...],
    "events": [...]
  }
}
```
