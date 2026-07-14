"""Guards the response shapes frontend/index.html depends on. If a service changes
its output keys, this fails so the (CI-untestable) dashboard doesn't silently break."""
from datetime import datetime, timezone

from rca.config import Settings as RcaSettings
from rca.correlate import correlate
from rca.graph import Graph
from rca.symptoms import Symptom

BASE = datetime(2026, 7, 14, tzinfo=timezone.utc)


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
