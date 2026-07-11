"""SQLite connection handling and schema. Plain sqlite3 + WAL — no ORM, so the
ML team can read the same DB directly with pandas/SQL."""
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS interface_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    node_id TEXT NOT NULL,
    interface TEXT NOT NULL,
    utilization_pct REAL NOT NULL,
    latency_ms REAL NOT NULL,
    jitter_ms REAL NOT NULL,
    packet_loss_pct REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ifmetrics_node_ts ON interface_metrics (node_id, ts);
CREATE INDEX IF NOT EXISTS idx_ifmetrics_ts ON interface_metrics (ts);

CREATE TABLE IF NOT EXISTS tunnel_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    tunnel_id TEXT NOT NULL,
    src_node TEXT NOT NULL,
    dst_node TEXT NOT NULL,
    state TEXT NOT NULL,
    throughput_mbps REAL NOT NULL,
    latency_ms REAL NOT NULL,
    encap_errors INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tunnel_ts ON tunnel_metrics (tunnel_id, ts);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    node_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts);
CREATE INDEX IF NOT EXISTS idx_events_node_ts ON events (node_id, ts);
"""


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        conn.commit()


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
