"""Golden-path end-to-end demo of the whole DRISHTI pipeline, fully offline.

Chains the real library layers of every service in one process — no network, no
running uvicorns, no Ollama required:

    synthetic backend events        (what the simulator would emit)
      -> rca.symptoms.build_symptoms + enrich_with_predictions (ml enrichment)
      -> rca.correlate.correlate     (root cause + cascade)
      -> copilot.explain.explain     (runbook retrieval + LLM / templated narrative)

Run it as a script to print each stage:

    python -m scripts.pipeline_demo

If a local Ollama is reachable it is used for the narrative; otherwise the
copilot degrades to its deterministic templated summary (llm_available=False),
so the demo always produces output on an air-gapped box.

The `run_golden_path` coroutine is imported by tests/test_e2e_pipeline.py as the
single cross-service integration check.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from copilot.explain import explain
from copilot.llm import ChatResult
from copilot.rag import Retriever
from rca.config import Settings as RcaSettings
from rca.correlate import correlate
from rca.graph import Graph
from rca.symptoms import build_symptoms, enrich_with_predictions

DEFAULT_RUNBOOKS = Path("data/runbooks")


class OfflineClient:
    """Stand-in LLM client that is always unavailable — mirrors what OllamaClient
    returns when the server is down, so the copilot's graceful-degradation path
    runs deterministically without touching the network."""

    model = "offline"

    async def chat(self, system: str, user: str) -> ChatResult:
        return ChatResult(content="", model=self.model, available=False)


def golden_path_events(as_of: datetime | None = None) -> tuple[list[dict], list[dict], datetime]:
    """A textbook cascade: a core P-router optic degrades first, and a PE edge
    router shows congestion ~30s later. Returns (events, ml_predictions, as_of).

    The ml predictions are the shape ml (:8200) publishes — folded into symptoms
    and cascade estimates by the rca layer as best-effort enrichment."""
    base = as_of - timedelta(seconds=30) if as_of else datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)

    def ev(node, offset, severity, scenario, message):
        return {
            "node_id": node,
            "ts": (base + timedelta(seconds=offset)).isoformat(),
            "severity": severity,
            "event_type": "syslog",
            "message": message,
            "details": {"scenario": scenario, "fault_id": "demo-cascade-1"},
        }

    events = [
        ev("p-core-1", 0, "error", "link_degradation", "CRC/input errors increasing on core optic Gi0/0"),
        ev("p-core-1", 5, "error", "link_degradation", "interface flapping, optical Rx power dropping"),
        ev("pe-east", 30, "warning", "congestion", "egress queue drops rising on PE uplink"),
    ]
    predictions = [
        {"node_id": "p-core-1", "interface": "Gi0/0", "precursor_probability": 0.92,
         "estimated_seconds_to_impact": 18.0},
        {"node_id": "pe-east", "interface": "Gi0/1", "precursor_probability": 0.74,
         "estimated_seconds_to_impact": 33.0},
        # pe-west has no symptom yet — a precursor flag on a still-downstream node,
        # so its eta flows through the RCA cascade (not the symptom set).
        {"node_id": "pe-west", "interface": "Gi0/1", "precursor_probability": 0.61,
         "estimated_seconds_to_impact": 45.0},
    ]
    last = max(datetime.fromisoformat(e["ts"]) for e in events)
    return events, predictions, last


def build_incident(
    events: list[dict],
    predictions: list[dict],
    as_of: datetime,
    graph: Graph | None = None,
    settings: RcaSettings | None = None,
) -> dict | None:
    """events + ml predictions -> the top (newest) correlated incident, as a dict.
    Returns None if nothing correlates above the symptom-weight floor."""
    graph = graph or Graph.from_path("data/topology.json")
    settings = settings or RcaSettings()
    symptoms = build_symptoms(events, as_of, settings.decay_tau_seconds, valid_nodes=set(graph.nodes))
    enrich_with_predictions(symptoms, predictions)
    node_estimates = {
        p["node_id"]: p.get("estimated_seconds_to_impact")
        for p in predictions
        if p.get("estimated_seconds_to_impact") is not None
    }
    incidents = correlate(symptoms, graph, settings, node_estimates)
    return incidents[0].to_dict() if incidents else None


async def explain_incident(
    incident: dict,
    llm_client=None,
    runbooks_dir: Path | str = DEFAULT_RUNBOOKS,
    top_k: int = 3,
) -> dict:
    """RCA incident -> grounded operator narrative (copilot layer)."""
    retriever = Retriever.from_dir(runbooks_dir)
    return await explain(incident, retriever, llm_client or OfflineClient(), top_k)


async def run_golden_path(
    llm_client=None,
    runbooks_dir: Path | str = DEFAULT_RUNBOOKS,
    as_of: datetime | None = None,
) -> dict:
    """Run the full offline pipeline for the built-in golden-path scenario.
    Returns {"events", "predictions", "incident", "explanation"}."""
    events, predictions, last = golden_path_events(as_of)
    incident = build_incident(events, predictions, last)
    if incident is None:
        raise RuntimeError("golden-path scenario produced no incident — pipeline regression")
    explanation = await explain_incident(incident, llm_client, runbooks_dir)
    return {
        "events": events,
        "predictions": predictions,
        "incident": incident,
        "explanation": explanation,
    }


def _rule(title: str) -> str:
    return f"\n{'=' * 4} {title} {'=' * (72 - len(title))}"


async def main() -> None:
    # The copilot logs Ollama failures with a stack trace by design; for the
    # demo we only care that it degraded, so keep that noise out of the output.
    import logging

    logging.getLogger("copilot.llm").setLevel(logging.CRITICAL)

    # Best-effort real LLM: use it if a local Ollama answers, else degrade.
    llm_client = None
    try:
        import httpx

        from copilot.config import get_settings
        from copilot.llm import OllamaClient

        settings = get_settings()
        http = httpx.AsyncClient()
        llm_client = OllamaClient(
            settings.ollama_url, settings.model, settings.num_predict,
            settings.num_ctx, settings.temperature, http,
        )
    except Exception:
        http = None

    try:
        result = await run_golden_path(llm_client)
    finally:
        if http is not None:
            await http.aclose()

    inc = result["incident"]
    exp = result["explanation"]

    print(_rule("1. SIMULATOR — raw events"))
    for e in result["events"]:
        print(f"  [{e['severity']:<8}] {e['ts']}  {e['node_id']:<9} {e['message']}")

    print(_rule("2. ML — precursor predictions"))
    for p in result["predictions"]:
        print(f"  {p['node_id']:<9} p(precursor)={p['precursor_probability']:.2f}  "
              f"~{p['estimated_seconds_to_impact']:.0f}s to impact")

    print(_rule("3. RCA — correlated incident"))
    rc = inc["root_cause"]
    print(f"  incident {inc['incident_id']}  severity={inc['severity']}")
    print(f"  root cause: {rc['node_id']} ({rc['anchor_type']} {rc['anchor_id']}) "
          f"confidence={rc['confidence']}")
    print(f"  rationale : {'; '.join(rc['rationale'])}")
    print(f"  symptoms  : {len(inc['symptoms'])}   cascade at-risk: {len(inc['cascade'])}")
    for c in inc["cascade"][:5]:
        eta = c.get("estimated_seconds_to_impact")
        eta_s = f"  (~{eta:.0f}s)" if eta is not None else ""
        print(f"    - {c['anchor_type']} {c['anchor_id']}: {c['at_risk_reason']}{eta_s}")

    print(_rule("4. COPILOT — operator narrative"))
    print(f"  model={exp['model']}  llm_available={exp['llm_available']}")
    print(f"  runbooks: {', '.join(r['runbook'] for r in exp['retrieved_runbooks']) or '(none)'}")
    print()
    for line in exp["narrative"].splitlines():
        print(f"  {line}")
    print()

    if "--json" in sys.argv:
        print(_rule("raw JSON"))
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
