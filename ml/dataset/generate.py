"""Data-gen harness: drives fault injection against the simulator (:8100), then
builds a labelled training set from data/drishti.db. Run manually — assumes the
simulator and backend are already running (see ml/README.md). Not a service."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import httpx
import pandas as pd

from ml.config import get_settings
from ml.features import compute_features
from ml.labels import is_in_ramp_window, seconds_to_impact

SCENARIOS = ["congestion_ramp", "bgp_flap_precursor", "link_degradation"]
RAMP_SECONDS = 20
HOLD_SECONDS = 10
BASELINE_GAP_SECONDS = 60


def load_topology(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def node_interfaces(topology: dict) -> dict[str, list[str]]:
    """node_id -> interface names, derived from each link's a/b endpoints."""
    result: dict[str, list[str]] = {}
    for link in topology["links"]:
        for end in (link["a"], link["b"]):
            result.setdefault(end["node"], []).append(end["interface"])
    return result


def inject_fault(
    client: httpx.Client,
    scenario: str,
    node_id: str,
    interface: str,
    ramp_seconds: int,
    hold_seconds: int,
) -> dict:
    """POSTs /faults with retries; raises after 3 failed attempts."""
    body = {
        "scenario": scenario,
        "node_id": node_id,
        "interface": interface,
        "params": {"ramp_seconds": ramp_seconds, "hold_seconds": hold_seconds},
    }
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = client.post("/faults", json=body, timeout=10.0)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            last_error = exc
            time.sleep(2**attempt)
    raise RuntimeError(f"failed to inject {scenario} on {node_id}/{interface}") from last_error


def clear_fault(client: httpx.Client, fault_id: str) -> None:
    """Best-effort clear — a fault that fails to clear self-expires via hold_seconds,
    so this must never raise (it always runs, success or failure, per-run)."""
    try:
        client.delete(f"/faults/{fault_id}", timeout=10.0)
    except httpx.HTTPError:
        pass


def run_harness(simulator_url: str, topology_path: Path, manifest_path: Path) -> list[dict]:
    topology = load_topology(topology_path)
    targets = node_interfaces(topology)
    manifest: list[dict] = []
    with httpx.Client(base_url=simulator_url) as client:
        for scenario in SCENARIOS:
            for node_id, interfaces in targets.items():
                interface = interfaces[0]
                fault = None
                try:
                    fault = inject_fault(client, scenario, node_id, interface, RAMP_SECONDS, HOLD_SECONDS)
                    manifest.append({
                        "fault_id": fault["fault_id"],
                        "scenario": scenario,
                        "node_id": node_id,
                        "interface": interface,
                        "started_at": fault["started_at"],
                        "ramp_seconds": RAMP_SECONDS,
                        "hold_seconds": HOLD_SECONDS,
                    })
                    time.sleep(RAMP_SECONDS + HOLD_SECONDS)
                finally:
                    if fault is not None:
                        clear_fault(client, fault["fault_id"])
                time.sleep(BASELINE_GAP_SECONDS)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest


def load_metrics_and_events(db_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    conn = sqlite3.connect(db_path)
    try:
        metrics = pd.read_sql_query(
            "SELECT ts, node_id, interface, utilization_pct, latency_ms, jitter_ms, "
            "packet_loss_pct FROM interface_metrics ORDER BY ts",
            conn,
        )
        events = pd.read_sql_query(
            "SELECT ts, node_id, severity FROM events ORDER BY ts", conn,
        )
    finally:
        conn.close()
    metrics["ts"] = pd.to_datetime(metrics["ts"], utc=True)
    events["ts"] = pd.to_datetime(events["ts"], utc=True)
    return metrics, events


def build_training_set(manifest: list[dict], metrics: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    manifest_by_key: dict[tuple[str, str], list[dict]] = {}
    for entry in manifest:
        manifest_by_key.setdefault((entry["node_id"], entry["interface"]), []).append(entry)

    rows: list[dict] = []
    for (node_id, interface), group in metrics.groupby(["node_id", "interface"]):
        group = group.sort_values("ts").reset_index(drop=True)
        node_events = events[events["node_id"] == node_id]
        entries = manifest_by_key.get((node_id, interface), [])
        for i in range(len(group)):
            as_of = group.loc[i, "ts"]
            feats = compute_features(group.iloc[: i + 1], node_events, as_of)
            if feats is None:
                continue
            match = next(
                (e for e in entries if is_in_ramp_window(
                    pd.Timestamp(e["started_at"]), e["ramp_seconds"], as_of)),
                None,
            )
            row = dict(feats)
            row["node_id"] = node_id
            row["interface"] = interface
            row["ts"] = as_of
            if match is None:
                row["is_precursor"] = 0
                row["seconds_to_impact"] = None
            else:
                row["is_precursor"] = 1
                row["seconds_to_impact"] = seconds_to_impact(
                    pd.Timestamp(match["started_at"]), match["ramp_seconds"], as_of,
                )
            rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    settings = get_settings()
    manifest = run_harness(
        settings.simulator_url, settings.topology_path, settings.dataset_dir / "manifest.json",
    )
    metrics, events = load_metrics_and_events(settings.db_path)
    training_set = build_training_set(manifest, metrics, events)
    settings.dataset_dir.mkdir(parents=True, exist_ok=True)
    out_path = settings.dataset_dir / "training.parquet"
    training_set.to_parquet(out_path, index=False)
    print(f"wrote {len(training_set)} rows to {out_path}")


if __name__ == "__main__":
    main()
