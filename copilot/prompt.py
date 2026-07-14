"""Renders an rca incident (+ retrieved runbook snippets) into (system, user) chat
messages. Pure + deterministic — unit-tested without the LLM."""
from __future__ import annotations

SYSTEM = (
    "You are DRISHTI, an offline assistant for a secure MPLS/SD-WAN NOC. The network "
    "has 6 nodes: two CE (ce-site-a, ce-site-b), two PE (pe-east, pe-west), two P core "
    "(p-core-1, p-core-2), with CE-to-CE IPsec tunnels riding the PE/P-core path. "
    "You are given a correlated incident from the graph RCA engine and relevant runbook "
    "excerpts. Explain it for an on-call operator. Rules: ground every claim in the "
    "provided RCA facts and runbook excerpts; never invent nodes, metrics, or events "
    "not present; be concise. Output exactly these sections:\n"
    "Summary:\nLikely root cause:\nBlast radius:\nRecommended checks:"
)


def _render_incident(incident: dict) -> str:
    rc = incident.get("root_cause", {})
    lines = [
        f"Incident {incident.get('incident_id', '?')} (severity {incident.get('severity', '?')}).",
        f"Root cause (RCA): {rc.get('anchor_type')} {rc.get('anchor_id')} on node "
        f"{rc.get('node_id')}, confidence {rc.get('confidence')}.",
        "RCA rationale: " + ("; ".join(rc.get("rationale", [])) or "n/a"),
        "",
        "Symptoms:",
    ]
    for s in incident.get("symptoms", []):
        scenario = s.get("scenario") or "unlabeled"
        msgs = "; ".join(s.get("sample_messages", []))
        lines.append(
            f"- {s.get('anchor_type')} {s.get('anchor_id')} (node {s.get('node_id')}, "
            f"max severity {s.get('severity_max')}, scenario {scenario}): {msgs}")
    lines.append("")
    lines.append("Predicted cascade (blast radius), nearest first:")
    for c in incident.get("cascade", []):
        lines.append(
            f"- {c.get('anchor_type')} {c.get('anchor_id')} (+{c.get('hops_from_root')} hops): "
            f"{c.get('at_risk_reason')}")
    return "\n".join(lines)


def _render_snippets(snippets: list[dict]) -> str:
    if not snippets:
        return "(no matching runbook excerpts)"
    blocks = []
    for s in snippets:
        blocks.append(f"[{s.get('runbook')} :: {s.get('heading')}]\n{s.get('text')}")
    return "\n\n".join(blocks)


def build_messages(incident: dict, snippets: list[dict]) -> tuple[str, str]:
    user = (
        "=== RCA INCIDENT ===\n"
        f"{_render_incident(incident)}\n\n"
        "=== RUNBOOK EXCERPTS ===\n"
        f"{_render_snippets(snippets)}\n\n"
        "Write the operator explanation now."
    )
    return SYSTEM, user
