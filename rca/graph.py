"""Pure-Python topology graph over data/topology.json. No service state, no I/O
beyond loading the file — every method is a deterministic function of the
topology. The graph is 6 nodes / 7 links, so all-pairs shortest paths and
betweenness are trivial to compute eagerly and cache."""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path


def load_topology(path: Path | str) -> dict:
    return json.loads(Path(path).read_text())


@dataclass(frozen=True)
class Edge:
    link_id: str
    kind: str
    bandwidth_mbps: int
    a: str
    b: str


class Graph:
    def __init__(self, topology: dict) -> None:
        self.topology = topology
        self.nodes: list[str] = [n["id"] for n in topology["nodes"]]
        self._adj: dict[str, list[tuple[str, Edge]]] = {n: [] for n in self.nodes}
        self._edges: list[Edge] = []
        for link in topology.get("links", []):
            a, b = link["a"]["node"], link["b"]["node"]
            edge = Edge(link["id"], link["kind"], link["bandwidth_mbps"], a, b)
            self._edges.append(edge)
            self._adj[a].append((b, edge))
            self._adj[b].append((a, edge))
        self._centrality = self._compute_centrality()

    @classmethod
    def from_topology(cls, topology: dict) -> "Graph":
        return cls(topology)

    @classmethod
    def from_path(cls, path: Path | str) -> "Graph":
        return cls(load_topology(path))

    # -- structure ------------------------------------------------------

    @property
    def tunnels(self) -> list[dict]:
        return self.topology.get("tunnels", [])

    @property
    def bgp_sessions(self) -> list[dict]:
        return self.topology.get("bgp_sessions", [])

    def neighbors(self, node: str) -> list[str]:
        return [peer for peer, _edge in self._adj.get(node, [])]

    # -- traversal ------------------------------------------------------

    def hops(self, src: str, dst: str) -> int | None:
        if src == dst:
            return 0
        if src not in self._adj or dst not in self._adj:
            return None
        seen = {src}
        queue: deque[tuple[str, int]] = deque([(src, 0)])
        while queue:
            node, dist = queue.popleft()
            for peer, _edge in self._adj[node]:
                if peer == dst:
                    return dist + 1
                if peer not in seen:
                    seen.add(peer)
                    queue.append((peer, dist + 1))
        return None

    def path_nodes(self, src: str, dst: str) -> list[str] | None:
        if src == dst:
            return [src]
        if src not in self._adj or dst not in self._adj:
            return None
        prev: dict[str, str | None] = {src: None}
        queue: deque[str] = deque([src])
        while queue:
            node = queue.popleft()
            if node == dst:
                break
            for peer, _edge in self._adj[node]:
                if peer not in prev:
                    prev[peer] = node
                    queue.append(peer)
        if dst not in prev:
            return None
        path: list[str] = []
        cur: str | None = dst
        while cur is not None:
            path.append(cur)
            cur = prev[cur]
        path.reverse()
        return path

    def tunnel_path(self, tunnel: dict) -> list[str] | None:
        return self.path_nodes(tunnel["src"], tunnel["dst"])

    def sessions_on(self, node: str) -> list[dict]:
        return [s for s in self.bgp_sessions if node in (s["a"], s["b"])]

    # -- centrality (normalized betweenness) ----------------------------

    def _compute_centrality(self) -> dict[str, float]:
        counts = {n: 0 for n in self.nodes}
        for i, src in enumerate(self.nodes):
            for dst in self.nodes[i + 1:]:
                path = self.path_nodes(src, dst)
                if path is None or len(path) <= 2:
                    continue
                for mid in path[1:-1]:
                    counts[mid] += 1
        mx = max(counts.values(), default=0)
        if mx == 0:
            return {n: 0.0 for n in self.nodes}
        return {n: counts[n] / mx for n in self.nodes}

    def centrality(self, node: str) -> float:
        return self._centrality.get(node, 0.0)

    # -- anchors --------------------------------------------------------

    def anchor_nodes(self, anchor_type: str, anchor_id: str) -> set[str]:
        if anchor_type == "node":
            return {anchor_id} if anchor_id in self._adj else set()
        if anchor_type == "link":
            for e in self._edges:
                if e.link_id == anchor_id:
                    return {e.a, e.b}
            return set()
        if anchor_type == "tunnel":
            for t in self.tunnels:
                if t["id"] == anchor_id:
                    path = self.tunnel_path(t)
                    return set(path) if path else {t["src"], t["dst"]}
            return set()
        if anchor_type == "bgp_session":
            for s in self.bgp_sessions:
                if s["id"] == anchor_id:
                    return {s["a"], s["b"]}
            return set()
        return set()

    def anchor_hops(self, t1: str, id1: str, t2: str, id2: str) -> int | None:
        ns1 = self.anchor_nodes(t1, id1)
        ns2 = self.anchor_nodes(t2, id2)
        best: int | None = None
        for x in ns1:
            for y in ns2:
                h = self.hops(x, y)
                if h is not None and (best is None or h < best):
                    best = h
        return best
