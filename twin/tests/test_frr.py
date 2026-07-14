from twin.addressing import load_topology
from twin.frr import frr_config

TOPO = load_topology("data/topology.json")


def test_frr_config_has_loopback_and_interfaces():
    cfg = frr_config("pe-east", TOPO, 65000)
    assert "hostname pe-east" in cfg
    assert "ip address 10.255.0.1/32" in cfg
    # pe-east has 3 links -> 3 physical interface blocks (+ lo)
    assert cfg.count("interface eth") == 3


def test_frr_config_has_bgp_neighbors_for_sessions():
    # pe-east participates in bgp-ce-a__pe-east (eBGP) + bgp-pe-east__pe-west (iBGP)
    cfg = frr_config("pe-east", TOPO, 65000)
    assert "router bgp 65000" in cfg
    assert cfg.count("neighbor ") >= 2


def test_core_node_runs_ospf():
    cfg = frr_config("p-core-1", TOPO, 65000)
    assert "router ospf" in cfg
