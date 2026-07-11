"""Persistence for network events (BGP/OSPF/syslog/tunnel)."""
import json
from datetime import datetime
from pathlib import Path

from app.repository.db import connect
from app.schemas import NetworkEvent


def insert_events(db_path: Path, events: list[NetworkEvent]) -> int:
    if not events:
        return 0
    rows = [
        (e.ts.isoformat(), e.node_id, e.severity, e.event_type, e.message,
         json.dumps(e.details))
        for e in events
    ]
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO events (ts, node_id, severity, event_type, message, details)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    return len(rows)


def query_events(
    db_path: Path,
    since: datetime,
    node_id: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    limit: int = 200,
) -> list[NetworkEvent]:
    sql = "SELECT * FROM events WHERE ts >= ?"
    params: list[object] = [since.isoformat()]
    if node_id:
        sql += " AND node_id = ?"
        params.append(node_id)
    if event_type:
        sql += " AND event_type = ?"
        params.append(event_type)
    if severity:
        sql += " AND severity = ?"
        params.append(severity)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    with connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        NetworkEvent(
            ts=row["ts"], node_id=row["node_id"], severity=row["severity"],
            event_type=row["event_type"], message=row["message"],
            details=json.loads(row["details"]),
        )
        for row in rows
    ]
