import pytest

from copilot.explain import build_query, explain
from copilot.llm import ChatResult
from copilot.rag import Snippet


def _incident():
    return {
        "incident_id": "inc-1", "severity": "warning",
        "root_cause": {"anchor_type": "node", "anchor_id": "p-core-1",
                       "node_id": "p-core-1", "confidence": 1.0, "rationale": ["central"]},
        "symptoms": [{"anchor_type": "node", "anchor_id": "p-core-1", "node_id": "p-core-1",
                      "severity_max": "warning", "scenario": "link_degradation",
                      "sample_messages": ["CRC/input errors increasing"]}],
        "cascade": [{"anchor_type": "tunnel", "anchor_id": "ipsec-a-to-b",
                     "hops_from_root": 0, "at_risk_reason": "rides the affected path"}],
    }


class _FakeRetriever:
    def retrieve(self, query, top_k):
        return [Snippet("link-degradation", "Symptoms", "CRC errors on the optic", 0.9)]


class _FakeClient:
    def __init__(self, result):
        self._result = result
        self.model = "mistral:7b"

    async def chat(self, system, user):
        return self._result


def test_build_query_includes_scenario_and_node():
    q = build_query(_incident())
    assert "p-core-1" in q
    assert "link_degradation" in q


@pytest.mark.anyio
async def test_explain_uses_llm_narrative_when_available():
    client = _FakeClient(ChatResult(content="Summary: p-core-1 degraded.", model="mistral:7b", available=True))
    result = await explain(_incident(), _FakeRetriever(), client, top_k=3)
    assert result["llm_available"] is True
    assert result["narrative"] == "Summary: p-core-1 degraded."
    assert result["root_cause_node"] == "p-core-1"
    assert result["retrieved_runbooks"][0]["runbook"] == "link-degradation"


@pytest.mark.anyio
async def test_explain_falls_back_when_llm_unavailable():
    client = _FakeClient(ChatResult(content="", model="mistral:7b", available=False))
    result = await explain(_incident(), _FakeRetriever(), client, top_k=3)
    assert result["llm_available"] is False
    assert "p-core-1" in result["narrative"]
    assert "link-degradation" in result["narrative"]
