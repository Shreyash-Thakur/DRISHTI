"""Deterministic IP + interface plan derived from data/topology.json, shared by
clab.py and frr.py so lab endpoints and FRR interface IPs always line up."""
from __future__ import annotations

import ipaddress
import json
from pathlib import Path

_LINK_POOL = "10.0.0.0/16"


def load_topology(path: Path | str) -> dict:
    return json.loads(Path(path).read_text())


def link_addressing(topology: dict) -> dict[str, dict]:
    subnets = ipaddress.ip_network(_LINK_POOL).subnets(new_prefix=30)
    result: dict[str, dict] = {}
    for link in topology["links"]:
        net = next(subnets)
        hosts = list(net.hosts())
        result[link["id"]] = {
            "subnet": str(net),
            "a_ip": str(hosts[0]),
            "b_ip": str(hosts[1]),
            "prefixlen": 30,
        }
    return result


def loopback_of(topology: dict, node_id: str) -> str:
    for node in topology["nodes"]:
        if node["id"] == node_id:
            loopback = node.get("loopback")
            if not loopback:
                raise ValueError(f"node {node_id!r} has no 'loopback' in topology.json")
            return loopback
    raise ValueError(f"unknown node {node_id!r}")


def role_of(topology: dict, node_id: str) -> str | None:
    return next((n.get("role") for n in topology["nodes"] if n["id"] == node_id), None)


def link_kind(topology: dict, link_id: str) -> str | None:
    return next((l["kind"] for l in topology["links"] if l["id"] == link_id), None)


def interface_plan(topology: dict) -> dict[str, list[dict]]:
    addr = link_addressing(topology)
    plan: dict[str, list[dict]] = {n["id"]: [] for n in topology["nodes"]}
    for link in topology["links"]:
        la = addr[link["id"]]
        a_node, b_node = link["a"]["node"], link["b"]["node"]
        a_if = f"eth{len(plan[a_node]) + 1}"
        plan[a_node].append({
            "ifname": a_if, "link_id": link["id"], "ip": la["a_ip"],
            "prefixlen": la["prefixlen"], "peer_node": b_node, "peer_ip": la["b_ip"],
        })
        b_if = f"eth{len(plan[b_node]) + 1}"
        plan[b_node].append({
            "ifname": b_if, "link_id": link["id"], "ip": la["b_ip"],
            "prefixlen": la["prefixlen"], "peer_node": a_node, "peer_ip": la["a_ip"],
        })
    return plan
