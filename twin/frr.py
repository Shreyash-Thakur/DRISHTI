"""Per-node FRR startup config, consistent with addressing.interface_plan. Emits
loopback + interface IPs, OSPF on core (P/PE) nodes, and BGP neighbors mirroring
topology.json's bgp_sessions. Single-ASN skeleton — refine eBGP AS on the host."""
from __future__ import annotations

import ipaddress

from twin.addressing import interface_plan, link_kind, loopback_of, role_of


def _network(ip: str, prefixlen: int) -> str:
    return str(ipaddress.ip_network(f"{ip}/{prefixlen}", strict=False).network_address)


def frr_config(node_id: str, topology: dict, asn: int) -> str:
    plan = interface_plan(topology)
    interfaces = plan[node_id]
    loopback = loopback_of(topology, node_id)

    lines = [
        "frr version 9.1",
        "frr defaults traditional",
        f"hostname {node_id}",
        "!",
        "interface lo",
        f" ip address {loopback}/32",
        "!",
    ]
    for iface in interfaces:
        lines += [
            f"interface {iface['ifname']}",
            f" ip address {iface['ip']}/{iface['prefixlen']}",
            "!",
        ]

    if role_of(topology, node_id) in ("P", "PE"):
        lines.append("router ospf")
        lines.append(f" network {loopback}/32 area 0")
        for iface in interfaces:
            if link_kind(topology, iface["link_id"]) == "core":
                net = _network(iface["ip"], iface["prefixlen"])
                lines.append(f" network {net}/{iface['prefixlen']} area 0")
        lines.append("!")

    sessions = [s for s in topology.get("bgp_sessions", []) if node_id in (s["a"], s["b"])]
    if sessions:
        lines.append(f"router bgp {asn}")
        lines.append(f" bgp router-id {loopback}")
        for session in sessions:
            peer = session["b"] if session["a"] == node_id else session["a"]
            direct = next((i["peer_ip"] for i in interfaces if i["peer_node"] == peer), None)
            neighbor_ip = direct if direct else loopback_of(topology, peer)
            lines.append(f" neighbor {neighbor_ip} remote-as {asn}")
        lines.append("!")

    return "\n".join(lines) + "\n"
