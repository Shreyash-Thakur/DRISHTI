"""Persistence for interface/tunnel timeseries."""
from datetime import datetime
from pathlib import Path

from app.repository.db import connect
from app.schemas import InterfaceMetric, TunnelMetric


def insert_interface_metrics(db_path: Path, metrics: list[InterfaceMetric]) -> int:
    if not metrics:
        return 0
    rows = [
        (m.ts.isoformat(), m.node_id, m.interface, m.utilization_pct,
         m.latency_ms, m.jitter_ms, m.packet_loss_pct)
        for m in metrics
    ]
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO interface_metrics"
            " (ts, node_id, interface, utilization_pct, latency_ms, jitter_ms, packet_loss_pct)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    return len(rows)


def insert_tunnel_metrics(db_path: Path, metrics: list[TunnelMetric]) -> int:
    if not metrics:
        return 0
    rows = [
        (m.ts.isoformat(), m.tunnel_id, m.src_node, m.dst_node, m.state,
         m.throughput_mbps, m.latency_ms, m.encap_errors)
        for m in metrics
    ]
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO tunnel_metrics"
            " (ts, tunnel_id, src_node, dst_node, state, throughput_mbps, latency_ms, encap_errors)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    return len(rows)


def query_interface_metrics(
    db_path: Path,
    node_id: str,
    since: datetime,
    interface: str | None = None,
    limit: int = 1000,
) -> list[InterfaceMetric]:
    sql = "SELECT * FROM interface_metrics WHERE node_id = ? AND ts >= ?"
    params: list[object] = [node_id, since.isoformat()]
    if interface:
        sql += " AND interface = ?"
        params.append(interface)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    with connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [InterfaceMetric(**{k: row[k] for k in row.keys() if k != "id"}) for row in reversed(rows)]


def query_tunnel_metrics(
    db_path: Path,
    node_id: str,
    since: datetime,
    limit: int = 1000,
) -> list[TunnelMetric]:
    sql = (
        "SELECT * FROM tunnel_metrics"
        " WHERE (src_node = ? OR dst_node = ?) AND ts >= ?"
        " ORDER BY ts DESC LIMIT ?"
    )
    with connect(db_path) as conn:
        rows = conn.execute(sql, (node_id, node_id, since.isoformat(), limit)).fetchall()
    return [TunnelMetric(**{k: row[k] for k in row.keys() if k != "id"}) for row in reversed(rows)]
