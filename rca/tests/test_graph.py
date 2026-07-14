from pathlib import Path

from rca.graph import Graph

TOPO = Path("data/topology.json")


def _graph() -> Graph:
    return Graph.from_path(TOPO)


def test_neighbors_and_hops_on_known_topology():
    g = _graph()
    assert set(g.neighbors("pe-east")) == {"ce-site-a", "p-core-1", "p-core-2"}
    assert g.hops("pe-east", "pe-east") == 0
    assert g.hops("ce-site-a", "pe-east") == 1
    # ce-a -> pe-east -> p-core -> pe-west -> ce-b
    assert g.hops("ce-site-a", "ce-site-b") == 4


def test_tunnel_path_traverses_pe_and_core():
    g = _graph()
    tunnel = next(t for t in g.tunnels if t["id"] == "ipsec-a-to-b")
    path = g.tunnel_path(tunnel)
    assert path[0] == "ce-site-a" and path[-1] == "ce-site-b"
    assert any(n.startswith("p-core") for n in path)
    assert "pe-east" in path and "pe-west" in path


def test_core_nodes_more_central_than_leaf():
    g = _graph()
    assert g.centrality("p-core-1") > g.centrality("ce-site-a")
    assert g.centrality("ce-site-a") == 0.0  # a leaf is on no intermediate path


def test_anchor_nodes_and_anchor_hops():
    g = _graph()
    assert g.anchor_nodes("node", "pe-east") == {"pe-east"}
    assert g.anchor_nodes("link", "pe-east__p-core-1") == {"pe-east", "p-core-1"}
    assert g.anchor_nodes("bgp_session", "bgp-ce-a__pe-east") == {"ce-site-a", "pe-east"}
    assert set(g.anchor_nodes("tunnel", "ipsec-a-to-b")) >= {"ce-site-a", "ce-site-b"}
    # hop between two node anchors
    assert g.anchor_hops("node", "p-core-1", "node", "pe-east") == 1
