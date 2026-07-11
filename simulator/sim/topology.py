"""Reads the shared topology file and flattens it into the specs the
generator and fault engine work with."""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class InterfaceSpec:
    node_id: str
    interface: str
    link_id: str
    kind: str  # "access" | "core"
    bandwidth_mbps: int
    peer_node: str


@dataclass(frozen=True)
class Topology:
    raw: dict[str, Any]
    interfaces: list[InterfaceSpec]

    @property
    def node_ids(self) -> set[str]:
        return {n["id"] for n in self.raw["nodes"]}

    @property
    def tunnels(self) -> list[dict[str, Any]]:
        return self.raw["tunnels"]

    @property
    def bgp_sessions(self) -> list[dict[str, Any]]:
        return self.raw["bgp_sessions"]

    def bgp_peer_of(self, node_id: str) -> tuple[str, str] | None:
        """Return (session_id, peer_node) for the first BGP session involving node_id."""
        for s in self.bgp_sessions:
            if node_id == s["a"]:
                return s["id"], s["b"]
            if node_id == s["b"]:
                return s["id"], s["a"]
        return None


def load_topology(path: Path) -> Topology:
    raw = json.loads(path.read_text())
    interfaces = []
    for link in raw["links"]:
        for end, other in (("a", "b"), ("b", "a")):
            interfaces.append(InterfaceSpec(
                node_id=link[end]["node"],
                interface=link[end]["interface"],
                link_id=link["id"],
                kind=link["kind"],
                bandwidth_mbps=link["bandwidth_mbps"],
                peer_node=link[other]["node"],
            ))
    return Topology(raw=raw, interfaces=interfaces)
