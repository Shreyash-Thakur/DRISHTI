"""In-memory rolling buffers + latest predictions, shared by the WS client
(writer, ml/service/ws_client.py) and the HTTP/WS routes (readers,
ml/service/routes.py). Single instance per process, both run on the same
asyncio event loop with no `await` mid-mutation, so no locking is needed."""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta

import pandas as pd

from ml.features import WINDOW_SECONDS

RETENTION = timedelta(seconds=WINDOW_SECONDS[-1] + 10)

_METRIC_COLUMNS = ["ts", "node_id", "interface", "utilization_pct", "latency_ms", "jitter_ms", "packet_loss_pct"]
_EVENT_COLUMNS = ["ts", "node_id", "severity"]


class LiveState:
    def __init__(self) -> None:
        self.metric_rows: dict[tuple[str, str], deque] = defaultdict(deque)
        self.event_rows: dict[str, deque] = defaultdict(deque)
        self.predictions: dict[tuple[str, str], dict] = {}

    def add_metric(self, row: dict) -> None:
        key = (row["node_id"], row["interface"])
        buf = self.metric_rows[key]
        buf.append(row)
        self._prune(buf, row["ts"])

    def add_event(self, row: dict) -> None:
        buf = self.event_rows[row["node_id"]]
        buf.append(row)
        self._prune(buf, row["ts"])

    @staticmethod
    def _prune(buf: deque, latest_ts: datetime) -> None:
        cutoff = latest_ts - RETENTION
        while buf and buf[0]["ts"] < cutoff:
            buf.popleft()

    def metric_frame(self, node_id: str, interface: str) -> pd.DataFrame:
        rows = list(self.metric_rows.get((node_id, interface), ()))
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=_METRIC_COLUMNS)

    def event_frame(self, node_id: str) -> pd.DataFrame:
        rows = list(self.event_rows.get(node_id, ()))
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=_EVENT_COLUMNS)

    def set_prediction(self, node_id: str, interface: str, prediction: dict) -> None:
        self.predictions[(node_id, interface)] = prediction

    def get_prediction(self, node_id: str, interface: str) -> dict | None:
        return self.predictions.get((node_id, interface))

    def predictions_for_node(self, node_id: str) -> list[dict]:
        return [p for (n, _i), p in self.predictions.items() if n == node_id]

    def all_predictions(self) -> list[dict]:
        return list(self.predictions.values())
