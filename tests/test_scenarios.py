"""Validates the edge-case scenario catalog (scripts/scenarios.py) so a scenario
can never reference a fault type or node that doesn't exist — a broken catalog
would only surface at demo time otherwise."""
from __future__ import annotations

import sys
from pathlib import Path

from rca.graph import Graph
from scripts.scenarios import SCENARIOS

_SIM_ROOT = str(Path(__file__).resolve().parents[1] / "simulator")
if _SIM_ROOT not in sys.path:
    sys.path.insert(0, _SIM_ROOT)

VALID_NODES = set(Graph.from_path("data/topology.json").nodes)


def _valid_fault_scenarios() -> set[str]:
    from sim.faults import SCENARIOS as FAULT_SCENARIOS
    return set(FAULT_SCENARIOS)


def test_every_step_uses_a_real_fault_and_node():
    faults = _valid_fault_scenarios()
    for name, s in SCENARIOS.items():
        assert s["description"] and s["expect"], f"{name} missing description/expect"
        assert s["steps"], f"{name} has no steps"
        for step in s["steps"]:
            assert step["scenario"] in faults, f"{name}: bad fault {step['scenario']!r}"
            assert step["node"] in VALID_NODES, f"{name}: bad node {step['node']!r}"


def test_multi_fault_edge_cases_present():
    # the catalog must include the adversarial multi-fault cases, not just singles
    multi = [n for n, s in SCENARIOS.items() if len(s["steps"]) > 1]
    assert {"dual-independent", "dual-core", "storm"} <= set(multi)


def test_dual_independent_targets_distant_leaves():
    steps = SCENARIOS["dual-independent"]["steps"]
    nodes = {s["node"] for s in steps}
    assert nodes == {"ce-site-a", "ce-site-b"}  # 4 hops apart -> must split into 2 incidents
