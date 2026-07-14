"""Edge-case simulation catalog + runner for a LIVE DRISHTI stack.

Each entry is a named scenario built from the simulator's fault primitives
(congestion_ramp / link_degradation / bgp_flap_precursor). Running one injects
the fault(s) into the simulator (:8100); the whole pipeline (ml -> rca ->
copilot) and the dashboard then react. Several are deliberately adversarial
edge cases used to stress the correlation logic.

    python -m scripts.scenarios list
    python -m scripts.scenarios run core-degradation
    python -m scripts.scenarios run dual-independent --wait 25   # poll rca and print the incidents
    python -m scripts.scenarios run resolve --wait 20            # watch an incident open then resolve
    python -m scripts.scenarios clear                            # remove all active faults

Use --sim / --rca to point at non-localhost hosts.
"""
from __future__ import annotations

import argparse
import sys
import time

import httpx

# A "step" is one fault injection, optionally delayed after the previous one.
#   {"scenario", "node", "interface"?, "params"?, "after"?(seconds)}
# Brisk ramps (short ramp_seconds) keep the precursor signal visible in a demo;
# large hold_seconds keeps the incident alive so there is time to explore it.
_PERSIST = {"ramp_seconds": 15, "hold_seconds": 900}

SCENARIOS: dict[str, dict] = {
    "core-degradation": {
        "description": "Failing optic on the core router p-core-1. Single central "
                       "root cause with a wide tunnel/BGP cascade.",
        "expect": "1 incident rooted at p-core-1, large cascade.",
        "steps": [{"scenario": "link_degradation", "node": "p-core-1", "params": _PERSIST}],
    },
    "edge-congestion": {
        "description": "Traffic surge saturating the edge router pe-east.",
        "expect": "1 incident rooted at pe-east; ml risk climbs during the ramp.",
        "steps": [{"scenario": "congestion_ramp", "node": "pe-east", "params": _PERSIST}],
    },
    "bgp-flap": {
        "description": "Accelerating BGP keepalive/hold-timer warnings on ce-site-a "
                       "ending in a session flap.",
        "expect": "1 incident carrying a bgp_session symptom.",
        "steps": [{"scenario": "bgp_flap_precursor", "node": "ce-site-a", "params": _PERSIST}],
    },
    "dual-independent": {
        "description": "EDGE CASE: two unrelated faults at the same time on leaf nodes "
                       "4 hops apart (ce-site-a and ce-site-b).",
        "expect": "2 SEPARATE incidents — they must NOT merge (topological clustering).",
        "steps": [
            {"scenario": "congestion_ramp", "node": "ce-site-a", "params": _PERSIST},
            {"scenario": "link_degradation", "node": "ce-site-b", "params": _PERSIST},
        ],
    },
    "dual-core": {
        "description": "EDGE CASE: both core routers (p-core-1, p-core-2) degrade "
                       "together — adjacent, so genuinely one event.",
        "expect": "1 incident spanning both cores (adjacent symptoms stay together).",
        "steps": [
            {"scenario": "link_degradation", "node": "p-core-1", "params": _PERSIST},
            {"scenario": "link_degradation", "node": "p-core-2", "params": _PERSIST},
        ],
    },
    "cascade-core-then-edge": {
        "description": "EDGE CASE: core degrades first, an edge router follows ~20s "
                       "later — the classic precursor-then-impact cascade.",
        "expect": "1 incident rooted at the (earlier) core, edge folded into the cascade.",
        "steps": [
            {"scenario": "link_degradation", "node": "p-core-1", "params": _PERSIST},
            {"scenario": "congestion_ramp", "node": "pe-east", "after": 20, "params": _PERSIST},
        ],
    },
    "storm": {
        "description": "EDGE CASE: simultaneous faults across the whole network "
                       "(both cores + both edges).",
        "expect": "Multiple incidents; the correlator must not collapse them into one.",
        "steps": [
            {"scenario": "link_degradation", "node": "p-core-1", "params": _PERSIST},
            {"scenario": "congestion_ramp", "node": "pe-east", "params": _PERSIST},
            {"scenario": "congestion_ramp", "node": "pe-west", "params": _PERSIST},
            {"scenario": "bgp_flap_precursor", "node": "ce-site-b", "params": _PERSIST},
        ],
    },
    "resolve": {
        "description": "EDGE CASE: a short-lived fault that ramps, holds briefly, then "
                       "auto-expires — exercises the incident open -> resolve lifecycle.",
        "expect": "An incident appears, then disappears from /incidents within ~30s.",
        "steps": [{"scenario": "congestion_ramp", "node": "pe-east",
                   "params": {"ramp_seconds": 6, "hold_seconds": 6}}],
    },
}


def list_scenarios() -> None:
    print("Available edge-case scenarios:\n")
    for name, s in SCENARIOS.items():
        print(f"  {name}")
        print(f"      {s['description']}")
        print(f"      expect: {s['expect']}\n")


def clear_faults(sim_url: str) -> None:
    try:
        r = httpx.request("DELETE", f"{sim_url}/faults", timeout=5.0)
        print(f"cleared faults: {r.json().get('cleared', '?')}")
    except Exception as exc:
        print(f"could not reach simulator at {sim_url}: {exc}")


def run_scenario(name: str, sim_url: str, rca_url: str, clear_first: bool, wait: float) -> int:
    scenario = SCENARIOS.get(name)
    if scenario is None:
        print(f"unknown scenario {name!r}. Try: python -m scripts.scenarios list")
        return 2

    print(f"== {name} ==\n{scenario['description']}\nexpect: {scenario['expect']}\n")
    if clear_first:
        clear_faults(sim_url)

    with httpx.Client() as client:
        for i, step in enumerate(scenario["steps"], 1):
            if step.get("after"):
                print(f"  …waiting {step['after']}s before step {i}")
                time.sleep(step["after"])
            body = {"scenario": step["scenario"], "node_id": step["node"]}
            if step.get("interface"):
                body["interface"] = step["interface"]
            if step.get("params"):
                body["params"] = step["params"]
            try:
                r = client.post(f"{sim_url}/faults", json=body, timeout=5.0)
                r.raise_for_status()
                f = r.json()
                print(f"  [{i}] injected {step['scenario']} on {step['node']} -> {f['fault_id']}")
            except Exception as exc:
                print(f"  [{i}] FAILED to inject {step['scenario']} on {step['node']}: {exc}")
                return 1

    print("\nWatch the dashboard (http://localhost:8080) — risk climbs, incident(s) "
          "appear, then click Explain.")

    if wait > 0:
        _poll_incidents(rca_url, wait)
    return 0


def _poll_incidents(rca_url: str, wait: float) -> None:
    print(f"\npolling rca for {wait:.0f}s…")
    deadline = time.monotonic() + wait
    last = None
    with httpx.Client() as client:
        while time.monotonic() < deadline:
            try:
                incidents = client.get(f"{rca_url}/incidents", timeout=5.0).json()
            except Exception as exc:
                print(f"  rca unreachable: {exc}")
                break
            summary = tuple(sorted(
                (i["incident_id"], i["root_cause"]["node_id"], i.get("severity"),
                 len(i.get("cascade", []))) for i in incidents))
            if summary != last:
                last = summary
                print(f"  [{len(incidents)} incident(s)] " + (
                    "; ".join(f"{iid}: root={root} sev={sev} cascade={casc}"
                              for iid, root, sev, casc in summary) or "none yet"))
            time.sleep(3.0)


def main() -> int:
    parser = argparse.ArgumentParser(description="DRISHTI edge-case simulation runner")
    parser.add_argument("command", choices=["list", "run", "clear"])
    parser.add_argument("name", nargs="?", help="scenario name (for `run`)")
    parser.add_argument("--sim", default="http://localhost:8100", help="simulator base URL")
    parser.add_argument("--rca", default="http://localhost:8300", help="rca base URL")
    parser.add_argument("--wait", type=float, default=0.0,
                        help="after running, poll rca for N seconds and print incidents")
    parser.add_argument("--no-clear", action="store_true", help="do not clear existing faults first")
    args = parser.parse_args()

    if args.command == "list":
        list_scenarios()
        return 0
    if args.command == "clear":
        clear_faults(args.sim)
        return 0
    if not args.name:
        print("`run` needs a scenario name. Try: python -m scripts.scenarios list")
        return 2
    return run_scenario(args.name, args.sim, args.rca, not args.no_clear, args.wait)


if __name__ == "__main__":
    sys.exit(main())
