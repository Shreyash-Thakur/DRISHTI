import yaml

from twin.addressing import load_topology
from twin.clab import build_clab, to_yaml

TOPO = load_topology("data/topology.json")


def test_build_clab_has_node_and_link_per_topology_entry():
    clab = build_clab(TOPO, "frr:9.1", "172.20.20.0/24")
    assert clab["name"] == "drishti"
    assert set(clab["topology"]["nodes"]) == {n["id"] for n in TOPO["nodes"]}
    assert len(clab["topology"]["links"]) == len(TOPO["links"])
    node = clab["topology"]["nodes"]["pe-east"]
    assert node["image"] == "frr:9.1"
    assert any("frr.conf" in b for b in node["binds"])


def test_clab_link_endpoints_reference_declared_interfaces():
    clab = build_clab(TOPO, "frr:9.1", "172.20.20.0/24")
    for link in clab["topology"]["links"]:
        assert len(link["endpoints"]) == 2
        for ep in link["endpoints"]:
            node, iface = ep.split(":")
            assert node in clab["topology"]["nodes"]
            assert iface.startswith("eth")


def test_to_yaml_round_trips():
    clab = build_clab(TOPO, "frr:9.1", "172.20.20.0/24")
    assert yaml.safe_load(to_yaml(clab)) == clab
