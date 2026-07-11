"""Loads the shared topology definition (data/topology.json) and answers
graph questions. The simulator reads the same file, so both sides always
agree on nodes/links."""
import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache
def _load(topology_path: str) -> dict[str, Any]:
    raw = json.loads(Path(topology_path).read_text())
    interfaces: dict[str, list[dict[str, str]]] = {n["id"]: [] for n in raw["nodes"]}
    for link in raw["links"]:
        for end, other in (("a", "b"), ("b", "a")):
            interfaces[link[end]["node"]].append({
                "interface": link[end]["interface"],
                "link_id": link["id"],
                "peer_node": link[other]["node"],
                "kind": link["kind"],
            })
    for node in raw["nodes"]:
        node["interfaces"] = interfaces[node["id"]]
    return raw


def get_topology(topology_path: Path) -> dict[str, Any]:
    return _load(str(topology_path))


def node_ids(topology_path: Path) -> set[str]:
    return {n["id"] for n in get_topology(topology_path)["nodes"]}
