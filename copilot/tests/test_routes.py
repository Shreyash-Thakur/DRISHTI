from fastapi import FastAPI
from fastapi.testclient import TestClient

from copilot.llm import ChatResult
from copilot.rag import Snippet
from copilot.service.routes import router


class _FakeRetriever:
    def retrieve(self, query, top_k):
        return [Snippet("link-degradation", "Symptoms", "CRC on optic", 0.9)]


class _FakeClient:
    model = "mistral:7b"

    async def chat(self, system, user):
        return ChatResult(content="Summary: p-core-1.", model="mistral:7b", available=True)


class _Settings:
    top_k = 3
    rca_url = "http://rca"


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.settings = _Settings()
    app.state.retriever = _FakeRetriever()
    app.state.llm = _FakeClient()
    app.state.http = None
    return TestClient(app)


def _incident():
    return {"incident_id": "inc-1", "severity": "warning",
            "root_cause": {"anchor_type": "node", "anchor_id": "p-core-1", "node_id": "p-core-1",
                           "confidence": 1.0, "rationale": ["central"]},
            "symptoms": [], "cascade": []}


def test_health():
    r = _client().get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "drishti-copilot"}


def test_explain_with_full_incident():
    r = _client().post("/explain", json={"incident": _incident()})
    assert r.status_code == 200
    body = r.json()
    assert body["root_cause_node"] == "p-core-1"
    assert body["llm_available"] is True
    assert body["narrative"].startswith("Summary")


def test_explain_requires_incident_or_id():
    r = _client().post("/explain", json={})
    assert r.status_code == 422
