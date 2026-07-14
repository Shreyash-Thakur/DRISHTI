"""Orchestration: incident -> retrieve runbooks -> build prompt -> LLM -> result.
When the LLM is unavailable, returns a deterministic templated narrative from the
RCA facts + top runbook headings so the operator still gets something useful."""
from __future__ import annotations

from copilot.prompt import build_messages


def build_query(incident: dict) -> str:
    rc = incident.get("root_cause", {})
    parts = [rc.get("node_id", ""), rc.get("anchor_type", "")]
    for symptom in incident.get("symptoms", []):
        if symptom.get("scenario"):
            parts.append(symptom["scenario"])
        parts.extend(symptom.get("sample_messages", []))
    return " ".join(p for p in parts if p)


def _fallback_narrative(incident: dict, snippets: list[dict]) -> str:
    rc = incident.get("root_cause", {})
    lines = [
        "[LLM offline — templated summary from RCA facts]",
        f"Likely root cause: {rc.get('node_id')} "
        f"({rc.get('anchor_type')} {rc.get('anchor_id')}), confidence {rc.get('confidence')}.",
        "Rationale: " + ("; ".join(rc.get("rationale", [])) or "n/a"),
    ]
    cascade = incident.get("cascade", [])
    if cascade:
        lines.append("At risk: " + ", ".join(
            f"{c.get('anchor_id')} (+{c.get('hops_from_root')}h)" for c in cascade[:5]))
    if snippets:
        lines.append("Relevant runbooks: " + ", ".join(
            f"{s.get('runbook')}#{s.get('heading')}" for s in snippets))
    return "\n".join(lines)


async def explain(incident: dict, retriever, client, top_k: int) -> dict:
    snippets = [s.to_dict() for s in retriever.retrieve(build_query(incident), top_k)]
    system, user = build_messages(incident, snippets)
    result = await client.chat(system, user)
    if result.available and result.content:
        narrative = result.content
    else:
        narrative = _fallback_narrative(incident, snippets)
    return {
        "incident_id": incident.get("incident_id"),
        "root_cause_node": incident.get("root_cause", {}).get("node_id"),
        "model": result.model,
        "llm_available": result.available,
        "narrative": narrative,
        "retrieved_runbooks": [
            {"runbook": s["runbook"], "heading": s["heading"], "score": s["score"]}
            for s in snippets
        ],
    }
