# Phase 5 Digital-Twin Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a deployable Containerlab digital twin (`drishti.clab.yml` + per-node FRR configs) from `data/topology.json`, fully offline and unit-tested, per `docs/superpowers/specs/2026-07-14-phase5-digital-twin-generator-design.md`.

**Architecture:** Pure-Python generator: `addressing.py` builds a deterministic IP plan (loopbacks from topology, per-link /30s) and the shared node→interface mapping; `clab.py` emits the Containerlab topology dict + YAML; `frr.py` emits per-node FRR startup configs consistent with that addressing; `generate.py` is a CLI that writes the artifacts. No live `containerlab deploy` in code — that's a documented manual step (toolchain not present offline).

**Tech Stack:** Python 3.11+, pydantic-settings, pyyaml (installed), stdlib `ipaddress`, pytest. Not a service — no FastAPI, no port.

## Global Constraints

- Offline, deterministic: same `topology.json` → byte-identical artifacts every run.
- `twin/` reads the same `data/topology.json` as every other component (no drift).
- Loopbacks come from each node's `loopback` field; link `/30`s from `10.0.0.0/16` in link order (`a`→`.1`, `b`→`.2`).
- Interface names (`eth1`, `eth2`, …) are assigned per node in link order and shared identically by `clab.py` and `frr.py`.
- `twin/lab/` is git-ignored (generated). `docker-compose.yml` is NOT modified (twin isn't a compose service).
- Single ASN for all BGP neighbors (a deliberate twin-skeleton simplification; a human refines eBGP AS numbers on the toolchain host).

---

## Task 1: `twin/` scaffolding + addressing (`twin/config.py`, `twin/addressing.py`)

**Files:** Create `twin/__init__.py`, `twin/config.py`, `twin/addressing.py`, `twin/requirements.txt`, `twin/pytest.ini`, `twin/tests/__init__.py`, `twin/tests/test_config.py`, `twin/tests/test_addressing.py`; Modify `.gitignore`.

**Interfaces:** `twin.config.Settings` (`topology_path`, `out_dir`, `node_image`, `mgmt_subnet`, `asn`) + `get_settings()`. `twin.addressing.load_topology(path) -> dict`, `link_addressing(topology) -> dict[str, dict]` (`{link_id: {subnet, a_ip, b_ip, prefixlen}}`), `loopback_of(topology, node_id) -> str`, `interface_plan(topology) -> dict[str, list[dict]]` (`node_id -> [{ifname, link_id, ip, prefixlen, peer_node, peer_ip}]`), `role_of(topology, node_id) -> str | None`, `link_kind(topology, link_id) -> str | None`.

- [ ] **Step 1: `twin/requirements.txt`**

```
pydantic>=2.6
pydantic-settings>=2.2
pyyaml>=6.0
pytest>=8.0
```

- [ ] **Step 2: `twin/config.py`**

```python
"""Settings for the twin generator — reads the shared topology, writes lab
artifacts."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TWIN_")

    topology_path: Path = Path("data/topology.json")
    out_dir: Path = Path("twin/lab")
    node_image: str = "quay.io/frrouting/frr:9.1.0"
    mgmt_subnet: str = "172.20.20.0/24"
    asn: int = 65000


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 3: `twin/pytest.ini`** — `[pytest]\ntestpaths = tests`; empty `twin/__init__.py`, `twin/tests/__init__.py`.

- [ ] **Step 4: `.gitignore`** — append:

```
# twin generated lab (regenerable)
twin/lab/
```

- [ ] **Step 5: `twin/tests/test_config.py`**

```python
from twin.config import Settings, get_settings


def test_defaults():
    s = Settings()
    assert s.asn == 65000
    assert str(s.topology_path).endswith("topology.json")


def test_cached():
    assert get_settings() is get_settings()
```

- [ ] **Step 6: `twin/tests/test_addressing.py`**

```python
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
    # every interface has a peer and an IP in a /30
    for i in plan["pe-east"]:
        assert i["peer_node"] and i["ip"] and i["peer_ip"]
```

- [ ] **Step 7: Implement `twin/addressing.py`**

```python
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
```

- [ ] **Step 8: run** — `cd twin && python -m pip install -r requirements.txt && cd .. && python -m pytest twin/tests -v` → `5 passed`
- [ ] **Step 9: Commit** — `git add twin/ .gitignore && git commit -m "twin: scaffold package, settings, deterministic addressing plan"`

---

## Task 2: Containerlab file (`twin/clab.py`)

**Files:** Create `twin/clab.py`; Test `twin/tests/test_clab.py`.

**Interfaces:** `twin.clab.build_clab(topology, node_image, mgmt_subnet) -> dict`, `twin.clab.to_yaml(clab: dict) -> str`.

- [ ] **Step 1: `twin/tests/test_clab.py`**

```python
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
```

- [ ] **Step 2: Run to verify fail**
- [ ] **Step 3: Implement `twin/clab.py`**

```python
"""Builds the Containerlab topology (drishti.clab.yml) from data/topology.json.
Interface names come from the shared addressing.interface_plan so endpoints match
the FRR configs generated by frr.py."""
from __future__ import annotations

import yaml

from twin.addressing import interface_plan


def build_clab(topology: dict, node_image: str, mgmt_subnet: str) -> dict:
    plan = interface_plan(topology)
    nodes: dict[str, dict] = {}
    for node in topology["nodes"]:
        nid = node["id"]
        nodes[nid] = {
            "kind": "linux",
            "image": node_image,
            "binds": [f"configs/{nid}/frr.conf:/etc/frr/frr.conf"],
        }
    links: list[dict] = []
    for link in topology["links"]:
        a_node, b_node = link["a"]["node"], link["b"]["node"]
        a_if = next(i["ifname"] for i in plan[a_node] if i["link_id"] == link["id"])
        b_if = next(i["ifname"] for i in plan[b_node] if i["link_id"] == link["id"])
        links.append({"endpoints": [f"{a_node}:{a_if}", f"{b_node}:{b_if}"]})
    return {
        "name": "drishti",
        "mgmt": {"network": "drishti-mgmt", "ipv4-subnet": mgmt_subnet},
        "topology": {"nodes": nodes, "links": links},
    }


def to_yaml(clab: dict) -> str:
    return yaml.safe_dump(clab, sort_keys=False, default_flow_style=False)
```

- [ ] **Step 4: Run to verify pass** → `3 passed`
- [ ] **Step 5: Commit** — `git add twin/clab.py twin/tests/test_clab.py && git commit -m "twin: add Containerlab topology-file generator"`

---

## Task 3: FRR configs (`twin/frr.py`)

**Files:** Create `twin/frr.py`; Test `twin/tests/test_frr.py`.

**Interfaces:** `twin.frr.frr_config(node_id, topology, asn) -> str`.

- [ ] **Step 1: `twin/tests/test_frr.py`**

```python
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
```

- [ ] **Step 2: Run to verify fail**
- [ ] **Step 3: Implement `twin/frr.py`**

```python
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
```

- [ ] **Step 4: Run to verify pass** → `3 passed`
- [ ] **Step 5: Commit** — `git add twin/frr.py twin/tests/test_frr.py && git commit -m "twin: add per-node FRR startup config generator"`

---

## Task 4: CLI + docs + verification (`twin/generate.py`, READMEs)

**Files:** Create `twin/generate.py`, `twin/README.md`; Test `twin/tests/test_generate.py`; Modify root `README.md`.

**Interfaces:** `twin.generate.write_lab(topology, settings) -> Path` (returns clab file path) + `twin.generate.main()`.

- [ ] **Step 1: `twin/tests/test_generate.py`**

```python
import yaml

from twin.addressing import load_topology
from twin.config import Settings
from twin.generate import write_lab

TOPO = load_topology("data/topology.json")


def test_write_lab_produces_clab_and_per_node_configs(tmp_path):
    settings = Settings(out_dir=tmp_path)
    clab_path = write_lab(TOPO, settings)
    assert clab_path.exists()
    clab = yaml.safe_load(clab_path.read_text())          # valid YAML
    assert clab["name"] == "drishti"
    for node in TOPO["nodes"]:
        cfg = tmp_path / "configs" / node["id"] / "frr.conf"
        assert cfg.exists()
        assert f"hostname {node['id']}" in cfg.read_text()
```

- [ ] **Step 2: Run to verify fail**
- [ ] **Step 3: Implement `twin/generate.py`**

```python
"""CLI: data/topology.json -> <out_dir>/drishti.clab.yml + per-node FRR configs.
Run: `python -m twin.generate`. Deploy on a containerlab host with the printed
command (containerlab + an FRR image are NOT required to generate)."""
from __future__ import annotations

from pathlib import Path

from twin.addressing import load_topology
from twin.clab import build_clab, to_yaml
from twin.config import Settings, get_settings
from twin.frr import frr_config


def write_lab(topology: dict, settings: Settings) -> Path:
    out = Path(settings.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    clab = build_clab(topology, settings.node_image, settings.mgmt_subnet)
    clab_path = out / "drishti.clab.yml"
    clab_path.write_text(to_yaml(clab))
    for node in topology["nodes"]:
        cfg_dir = out / "configs" / node["id"]
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "frr.conf").write_text(frr_config(node["id"], topology, settings.asn))
    return clab_path


def main() -> None:
    settings = get_settings()
    topology = load_topology(settings.topology_path)
    clab_path = write_lab(topology, settings)
    print(f"wrote {clab_path} and per-node FRR configs under {settings.out_dir}/configs/")
    print(f"deploy on a containerlab host with: containerlab deploy -t {clab_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass** → `1 passed`; then run the full twin suite `python -m pytest twin/tests -v`.
- [ ] **Step 5: Real generation check** — `python -m twin.generate` from repo root, then confirm `twin/lab/drishti.clab.yml` loads as YAML and one `frr.conf` per node exists (this is the offline verification; it does NOT require containerlab).
- [ ] **Step 6: Write `twin/README.md`** — what the twin is, the scope caveat (containerlab + FRR image needed to *run*, not to *generate*), `python -m twin.generate`, the `containerlab deploy` command, running the tests, link to the design spec.
- [ ] **Step 7: Update root `README.md`** — roadmap item 5 → `5. ✅ Digital twin generator (Containerlab lab + FRR configs; live deploy needs the containerlab toolchain)`; repo-layout `twin/` bullet; teammates bullet. (No `:port`; not a service.)
- [ ] **Step 8: Commit** — `git add twin/generate.py twin/README.md twin/tests/test_generate.py README.md && git commit -m "twin: add lab-generation CLI, docs; Phase 5 generator"`

---

## Self-Review Notes

- **Spec coverage:** deterministic addressing incl. shared interface plan (Task 1), clab file generator + YAML (Task 2), per-node FRR configs w/ OSPF+BGP (Task 3), CLI + offline generation verification + docs (Task 4). Live deploy/validate intentionally out of scope (documented). All spec sections mapped.
- **No placeholders:** every code step is complete.
- **Type consistency:** `interface_plan` output keys (`ifname/link_id/ip/prefixlen/peer_node/peer_ip`) are read identically in `clab.py` and `frr.py`; `build_clab`/`to_yaml`/`frr_config`/`write_lab` signatures match their call sites and tests; `loopback_of`/`role_of`/`link_kind` used consistently.
- **Honest scope:** nothing here invokes `containerlab`; the only "verification" claimed is deterministic artifact generation + YAML validity, which is exactly what's runnable offline.
