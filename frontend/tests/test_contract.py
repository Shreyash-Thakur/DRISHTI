"""Guards the response shapes frontend/index.html depends on. If a service changes
its output keys, this fails so the (CI-untestable) dashboard doesn't silently break."""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from rca.config import Settings as RcaSettings
from rca.correlate import correlate
from rca.graph import Graph
from rca.symptoms import Symptom

BASE = datetime(2026, 7, 14, tzinfo=timezone.utc)

# The simulator package lives under simulator/ (not importable from the repo root
# by default). The dashboard's fault-injection panel calls it, so guard its
# contract here too.
_SIM_ROOT = str(Path(__file__).resolve().parents[2] / "simulator")
if _SIM_ROOT not in sys.path:
    sys.path.insert(0, _SIM_ROOT)


def _simulator_client():
    from fastapi.testclient import TestClient

    os.environ.setdefault("SIM_TOPOLOGY_PATH", "data/topology.json")
    os.environ["SIM_BACKEND_URL"] = "http://127.0.0.1:9"  # unreachable; keep the telemetry loop dormant
    os.environ["SIM_INTERVAL_SECONDS"] = "3600"
    import sim.config as sim_config
    from sim.api import create_app

    sim_config.get_settings.cache_clear()
    return TestClient(create_app())  # no `with` → lifespan/telemetry loop stays off


def test_incident_shape_the_dashboard_reads():
    graph = Graph.from_path("data/topology.json")
    sym = Symptom("node", "p-core-1", "p-core-1", BASE, BASE, "error", 5.0, 1)
    incident = correlate([sym], graph, RcaSettings())[0].to_dict()
    # keys the incidents panel + Explain button rely on
    assert {"incident_id", "severity", "root_cause", "symptoms", "cascade"} <= incident.keys()
    assert {"node_id", "confidence", "rationale"} <= incident["root_cause"].keys()
    for entry in incident["cascade"]:
        assert {"anchor_type", "anchor_id", "hops_from_root"} <= entry.keys()


def test_explain_output_shape_the_dashboard_reads():
    import anyio

    from copilot.explain import explain
    from copilot.llm import ChatResult
    from copilot.rag import Snippet

    class _R:
        def retrieve(self, q, k):
            return [Snippet("link-degradation", "Symptoms", "CRC", 0.9)]

    class _C:
        model = "m"

        async def chat(self, s, u):
            return ChatResult(content="Summary: x", model="m", available=True)

    incident = {"incident_id": "i", "root_cause": {"node_id": "p-core-1"},
                "symptoms": [], "cascade": []}
    out = anyio.run(explain, incident, _R(), _C(), 3)
    assert {"narrative", "llm_available", "retrieved_runbooks", "root_cause_node"} <= out.keys()


def test_simulator_scenarios_shape_the_injector_reads():
    client = _simulator_client()
    body = client.get("/scenarios").json()
    # the injector populates a <select> from the scenario names + tooltips
    assert "congestion_ramp" in body
    assert {"description", "default_params"} <= body["congestion_ramp"].keys()


def test_simulator_inject_and_list_shape_the_injector_reads():
    client = _simulator_client()
    created = client.post("/faults", json={"scenario": "congestion_ramp", "node_id": "p-core-1"})
    assert created.status_code == 201
    # keys the injector's status line + active-faults list rely on
    assert {"fault_id", "scenario", "node_id", "interface"} <= created.json().keys()
    listed = client.get("/faults").json()
    assert any(f["fault_id"] == created.json()["fault_id"] for f in listed)


def test_simulator_allows_cross_origin_for_the_dashboard():
    client = _simulator_client()
    r = client.get("/scenarios", headers={"Origin": "http://localhost:8080"})
    assert r.headers.get("access-control-allow-origin") == "*"
