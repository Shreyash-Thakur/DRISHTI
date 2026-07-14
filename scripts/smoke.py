"""Live-stack smoke test — drives the real HTTP wiring end to end against
*running* services, the one path the in-process demo can't cover.

Unlike scripts/pipeline_demo.py (fully in-process, no servers), this expects the
stack to be up (docker compose up, or the plain-python quick-start) and:

  1. health-checks every service;
  2. injects a fast-ramping fault via the simulator (:8100);
  3. polls rca (:8300) until it correlates an incident from the resulting
     event stream (simulator -> backend WS -> rca);
  4. asks copilot (:8400) to explain that incident *by id*, which makes copilot
     fetch it from rca over HTTP — the cross-service call nothing else exercises.

Prints PASS/FAIL per step and exits non-zero if any required step fails, so it
doubles as a post-deploy check. ml (:8200) and the frontend (:8080) are probed
but optional — rca enriches with ml best-effort and degrades without it.

    python -m scripts.smoke                 # localhost defaults
    python -m scripts.smoke --timeout 120   # allow longer for the fault to ramp
    python -m scripts.smoke --node pe-east --scenario link_degradation
"""
from __future__ import annotations

import argparse
import sys
import time

import httpx

DEFAULTS = {
    "backend": "http://localhost:8000",
    "simulator": "http://localhost:8100",
    "ml": "http://localhost:8200",
    "rca": "http://localhost:8300",
    "copilot": "http://localhost:8400",
    "frontend": "http://localhost:8080",
}
REQUIRED = ("backend", "simulator", "rca", "copilot")


class Reporter:
    def __init__(self) -> None:
        self.failed = False

    def ok(self, step: str, detail: str = "") -> None:
        print(f"  [PASS] {step}{' — ' + detail if detail else ''}")

    def warn(self, step: str, detail: str = "") -> None:
        print(f"  [WARN] {step}{' — ' + detail if detail else ''}")

    def fail(self, step: str, detail: str = "") -> None:
        self.failed = True
        print(f"  [FAIL] {step}{' — ' + detail if detail else ''}")


def _health(client: httpx.Client, url: str) -> tuple[bool, str]:
    try:
        r = client.get(f"{url}/health", timeout=3.0)
        r.raise_for_status()
        body = r.json()
        return True, body.get("service", body.get("status", "ok"))
    except Exception as exc:
        return False, str(exc)


def check_health(client: httpx.Client, urls: dict[str, str], rep: Reporter) -> set[str]:
    print("1. Health checks")
    healthy: set[str] = set()
    for name in ("backend", "simulator", "ml", "rca", "copilot", "frontend"):
        url = urls[name]
        alive, detail = _health(client, url)
        if alive:
            healthy.add(name)
            rep.ok(f"{name} @ {url}", detail)
        elif name in REQUIRED:
            rep.fail(f"{name} @ {url}", "not reachable")
        else:
            rep.warn(f"{name} @ {url}", "not reachable (optional)")
    return healthy


def inject_fault(client: httpx.Client, sim_url: str, node: str, scenario: str, rep: Reporter) -> bool:
    print("2. Inject a fast-ramping fault via the simulator")
    try:
        client.delete(f"{sim_url}/faults", timeout=5.0)  # start clean
        r = client.post(
            f"{sim_url}/faults",
            json={"scenario": scenario, "node_id": node,
                  "params": {"ramp_seconds": 12, "hold_seconds": 600}},
            timeout=5.0,
        )
        r.raise_for_status()
        fault = r.json()
        rep.ok(f"injected {scenario} on {node}", f"fault_id={fault.get('fault_id')}")
        return True
    except Exception as exc:
        rep.fail("fault injection", str(exc))
        return False


def wait_for_incident(client: httpx.Client, rca_url: str, timeout: float, rep: Reporter) -> str | None:
    print(f"3. Wait for rca to correlate an incident (timeout {timeout:.0f}s)")
    deadline = time.monotonic() + timeout
    last_err = ""
    while time.monotonic() < deadline:
        try:
            r = client.get(f"{rca_url}/incidents", timeout=5.0)
            r.raise_for_status()
            incidents = r.json()
            if incidents:
                inc = incidents[0]
                rep.ok("incident correlated",
                       f"{inc['incident_id']} root={inc['root_cause']['node_id']} "
                       f"sev={inc.get('severity')} cascade={len(inc.get('cascade', []))}")
                return inc["incident_id"]
        except Exception as exc:
            last_err = str(exc)
        time.sleep(2.0)
    rep.fail("no incident within timeout", last_err or "rca returned an empty incident list")
    return None


def explain(client: httpx.Client, copilot_url: str, incident_id: str, rep: Reporter) -> bool:
    print("4. Ask copilot to explain the incident BY ID (copilot -> rca over HTTP)")
    try:
        r = client.post(f"{copilot_url}/explain", json={"incident_id": incident_id}, timeout=190.0)
        r.raise_for_status()
        out = r.json()
    except Exception as exc:
        rep.fail("copilot /explain", str(exc))
        return False
    if not out.get("narrative") or not out.get("root_cause_node"):
        rep.fail("copilot /explain", f"missing narrative/root_cause_node in {list(out)}")
        return False
    rep.ok("copilot explained the fetched incident",
           f"root={out['root_cause_node']} llm_available={out['llm_available']} "
           f"runbooks={[b['runbook'] for b in out.get('retrieved_runbooks', [])]}")
    print("\n   --- narrative ---")
    for line in out["narrative"].splitlines():
        print(f"   {line}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="DRISHTI live-stack smoke test")
    parser.add_argument("--timeout", type=float, default=90.0,
                        help="seconds to wait for an incident to correlate (default 90)")
    parser.add_argument("--node", default="p-core-1", help="node to fault (default p-core-1)")
    parser.add_argument("--scenario", default="congestion_ramp",
                        help="fault scenario (default congestion_ramp)")
    for name, url in DEFAULTS.items():
        parser.add_argument(f"--{name}-url", default=url, dest=f"{name}_url")
    args = parser.parse_args()

    urls = {name: getattr(args, f"{name}_url") for name in DEFAULTS}
    rep = Reporter()

    print("DRISHTI live-stack smoke test\n")
    with httpx.Client() as client:
        healthy = check_health(client, urls, rep)

        if not all(name in healthy for name in REQUIRED):
            print("\nRequired services are down — skipping the flow. "
                  "Bring the stack up (docker compose up) and re-run.")
            return 1

        print()
        if not inject_fault(client, urls["simulator"], args.node, args.scenario, rep):
            return 1

        print()
        incident_id = wait_for_incident(client, urls["rca"], args.timeout, rep)
        if incident_id is None:
            return 1

        print()
        explain(client, urls["copilot"], incident_id, rep)

    print("\n" + ("SMOKE FAILED" if rep.failed else "SMOKE PASSED"))
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
