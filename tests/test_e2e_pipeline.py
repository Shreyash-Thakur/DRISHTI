"""Cross-service end-to-end pipeline test — the only test that exercises more
than one service together. Every per-service suite proves its own layer; this
proves the layers compose on the golden-path cascade, fully offline.

    simulator events -> ml enrichment -> rca correlation -> copilot narrative

It intentionally uses the real topology (data/topology.json), the real runbook
corpus (data/runbooks/), and the real correlation/retrieval code. Only the two
things that would reach the network are stubbed: the LLM (forced offline via
OfflineClient, so we assert the graceful-degradation path) and the ml service
(its published prediction shape is fed in directly)."""
from __future__ import annotations

import pytest

from scripts.pipeline_demo import OfflineClient, golden_path_events, run_golden_path


def test_golden_path_events_are_a_two_stage_cascade():
    events, predictions, as_of = golden_path_events()
    # core degrades before the edge shows symptoms
    assert events[0]["node_id"] == "p-core-1"
    assert events[-1]["node_id"] == "pe-east"
    assert {p["node_id"] for p in predictions} == {"p-core-1", "pe-east", "pe-west"}
    assert as_of.isoformat() == max(e["ts"] for e in events)


@pytest.mark.anyio
async def test_pipeline_produces_grounded_offline_narrative():
    result = await run_golden_path(llm_client=OfflineClient())
    inc = result["incident"]
    exp = result["explanation"]

    # --- RCA layer: earliest + central node wins root cause ---
    assert inc["root_cause"]["node_id"] == "p-core-1"
    assert inc["root_cause"]["confidence"] > 0.5
    assert inc["severity"] in {"warning", "error", "critical"}

    # --- ML enrichment reached the symptoms (best-effort, no hard dep) ---
    core_symptoms = [s for s in inc["symptoms"] if s["node_id"] == "p-core-1"]
    assert core_symptoms and core_symptoms[0]["precursor_probability"] == pytest.approx(0.92)

    # --- Cascade: the CE-to-CE tunnels ride through the core, ml eta flows through ---
    cascade_tunnels = {c["anchor_id"] for c in inc["cascade"] if c["anchor_type"] == "tunnel"}
    assert "ipsec-a-to-b" in cascade_tunnels
    # pe-west is downstream (no symptom yet) — its precursor eta rides the cascade
    pe_west = [c for c in inc["cascade"] if c["node_id"] == "pe-west" and c["anchor_type"] == "node"]
    assert pe_west and pe_west[0]["estimated_seconds_to_impact"] == pytest.approx(45.0)

    # --- Copilot layer: offline fallback, still grounded in RCA facts + a runbook ---
    assert exp["llm_available"] is False
    assert exp["root_cause_node"] == "p-core-1"
    assert "p-core-1" in exp["narrative"]
    retrieved = {r["runbook"] for r in exp["retrieved_runbooks"]}
    assert "link-degradation" in retrieved


@pytest.mark.anyio
async def test_pipeline_uses_llm_narrative_when_available():
    from copilot.llm import ChatResult

    class _LiveClient:
        model = "mistral:7b"

        async def chat(self, system, user):
            return ChatResult(content="Core optic on p-core-1 is failing.", model=self.model, available=True)

    result = await run_golden_path(llm_client=_LiveClient())
    exp = result["explanation"]
    assert exp["llm_available"] is True
    assert exp["narrative"] == "Core optic on p-core-1 is failing."
    # retrieval still runs and grounds the (real) prompt
    assert exp["retrieved_runbooks"]
