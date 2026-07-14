from twin.addressing import (
    interface_plan, link_addressing, load_topology, loopback_of,
)

TOPO = load_topology("data/topology.json")


def test_link_addressing_distinct_slash30s():
    addr = link_addressing(TOPO)
    assert len(addr) == len(TOPO["links"])
    subnets = [a["subnet"] for a in addr.values()]
    assert len(set(subnets)) == len(subnets)          # all distinct
    first = addr[TOPO["links"][0]["id"]]
    assert first["a_ip"].endswith(".1") and first["b_ip"].endswith(".2")
    assert first["prefixlen"] == 30


def test_loopback_from_topology():
    assert loopback_of(TOPO, "pe-east") == "10.255.0.1"


def test_interface_plan_matches_link_degree():
    plan = interface_plan(TOPO)
    # pe-east touches ce-site-a, p-core-1, p-core-2 -> 3 interfaces
    assert len(plan["pe-east"]) == 3
    ifnames = [i["ifname"] for i in plan["pe-east"]]
    assert ifnames == ["eth1", "eth2", "eth3"]
    for i in plan["pe-east"]:
        assert i["peer_node"] and i["ip"] and i["peer_ip"]
