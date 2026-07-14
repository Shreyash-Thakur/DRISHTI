"""RCA core: cluster symptoms into incidents (temporal proximity), score
candidate root causes (earliest + centrality + explanatory reach), and predict
the cascade (ordered blast radius) from the chosen root. Deterministic and
explainable by design — the output feeds an LLM that must justify 'why'."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from rca.config import Settings
from rca.graph import Graph
from rca.symptoms import SEVERITY_RANK, Symptom, rank_to_severity


@dataclass
class Incident:
    incident_id: str
    opened_at: datetime
    updated_at: datetime
    status: str
    severity: str
    root_cause: dict
    symptoms: list[Symptom]
    cascade: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "incident_id": self.incident_id,
            "opened_at": self.opened_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "status": self.status,
            "severity": self.severity,
            "root_cause": self.root_cause,
            "symptoms": [s.to_dict() for s in self.symptoms],
            "cascade": self.cascade,
        }


def incident_signature(incident: dict) -> str:
    """Shape hash used to decide whether to re-publish — deliberately excludes
    timestamps and continuous weights so it changes only when a symptom, the
    root cause, the cascade, or severity changes."""
    rc = incident["root_cause"]
    parts = [
        f"rc={rc['anchor_type']}:{rc['anchor_id']}",
        "sym=" + ",".join(sorted(
            f"{s['anchor_type']}:{s['anchor_id']}" for s in incident["symptoms"])),
        "cas=" + ",".join(sorted(
            f"{c['anchor_type']}:{c['anchor_id']}" for c in incident["cascade"])),
        f"sev={incident['severity']}",
    ]
    return "|".join(parts)


def _incident_id(group: list[Symptom]) -> str:
    key = "|".join(sorted(f"{s.anchor_type}:{s.anchor_id}" for s in group))
    return "inc-" + hashlib.sha1(key.encode()).hexdigest()[:8]


def _find(parent: list[int], x: int) -> int:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _windows_close(a: Symptom, b: Symptom, window: float) -> bool:
    latest_start = max(a.first_seen, b.first_seen)
    earliest_end = min(a.last_seen, b.last_seen)
    if latest_start <= earliest_end:
        return True
    return (latest_start - earliest_end).total_seconds() <= window


def _topologically_close(a: Symptom, b: Symptom, graph: Graph, max_hops: int) -> bool:
    """Two symptoms can belong to the same incident only if one could plausibly
    cascade to the other — i.e. their anchors are within `max_hops` in the graph.
    Without this gate, two unrelated faults that merely coincide in time get
    merged into a single incident with a bogus shared root cause."""
    hops = graph.anchor_hops(a.anchor_type, a.anchor_id, b.anchor_type, b.anchor_id)
    return hops is not None and hops <= max_hops


def _cluster(
    symptoms: list[Symptom], window: float, graph: Graph, max_hops: int,
) -> list[list[Symptom]]:
    parent = list(range(len(symptoms)))
    for i in range(len(symptoms)):
        for j in range(i + 1, len(symptoms)):
            if (_windows_close(symptoms[i], symptoms[j], window)
                    and _topologically_close(symptoms[i], symptoms[j], graph, max_hops)):
                parent[_find(parent, i)] = _find(parent, j)
    groups: dict[int, list[Symptom]] = {}
    for i, symptom in enumerate(symptoms):
        groups.setdefault(_find(parent, i), []).append(symptom)
    return list(groups.values())


def _anchor_centrality(graph: Graph, symptom: Symptom) -> float:
    nodes = graph.anchor_nodes(symptom.anchor_type, symptom.anchor_id)
    return max((graph.centrality(n) for n in nodes), default=0.0)


def _score_root_cause(
    group: list[Symptom], graph: Graph, settings: Settings,
) -> tuple[Symptom, float, float, list[str]]:
    """Returns (root_symptom, top_score, runner_up_score, rationale)."""
    firsts = [s.first_seen for s in group]
    t_min, t_max = min(firsts), max(firsts)
    span = (t_max - t_min).total_seconds() or 1.0
    scored: list[tuple[Symptom, float, list[str]]] = []
    for symptom in group:
        others = [o for o in group if o is not symptom]
        earliest = 1.0 - ((symptom.first_seen - t_min).total_seconds() / span)
        central = _anchor_centrality(graph, symptom)
        if others:
            reachable = sum(
                1 for o in others
                if (h := graph.anchor_hops(symptom.anchor_type, symptom.anchor_id,
                                           o.anchor_type, o.anchor_id)) is not None
                and h <= settings.cascade_max_hops
            )
            reach = reachable / len(others)
        else:
            reach = 1.0
        score = (settings.w_earliest * earliest
                 + settings.w_central * central
                 + settings.w_reach * reach)
        rationale: list[str] = []
        if earliest >= 0.999 and others:
            rationale.append("earliest symptom in the incident")
        if central >= 0.5:
            rationale.append(f"central topology element (centrality {central:.2f})")
        if others and reach > 0:
            rationale.append(
                f"topologically upstream of {int(round(reach * len(others)))} "
                f"of {len(others)} other symptoms")
        scored.append((symptom, score, rationale))
    scored.sort(key=lambda item: item[1], reverse=True)
    top = scored[0]
    runner_up_score = scored[1][1] if len(scored) > 1 else 0.0
    rationale = top[2] or ["only / strongest symptom in the incident"]
    return top[0], top[1], runner_up_score, rationale


def _predict_cascade(
    root: Symptom, group: list[Symptom], graph: Graph, settings: Settings,
    node_impact_estimates: dict[str, float | None],
) -> list[dict]:
    symptomatic = {(s.anchor_type, s.anchor_id) for s in group}
    root_nodes = graph.anchor_nodes(root.anchor_type, root.anchor_id)
    entries: list[dict] = []

    for node in graph.nodes:
        if ("node", node) in symptomatic or node in root_nodes:
            continue
        distances = [h for rn in root_nodes if (h := graph.hops(rn, node)) is not None]
        if not distances:
            continue
        hops = min(distances)
        if hops == 0 or hops > settings.cascade_max_hops:
            continue
        entries.append({
            "anchor_type": "node", "anchor_id": node, "node_id": node,
            "hops_from_root": hops,
            "at_risk_reason": f"{hops} hop(s) downstream of the root cause",
            "estimated_seconds_to_impact": node_impact_estimates.get(node),
        })

    for tunnel in graph.tunnels:
        if ("tunnel", tunnel["id"]) in symptomatic:
            continue
        path = graph.tunnel_path(tunnel) or []
        if root_nodes & set(path):
            entries.append({
                "anchor_type": "tunnel", "anchor_id": tunnel["id"],
                "node_id": tunnel.get("src", ""),
                "hops_from_root": 0,
                "at_risk_reason": "rides the affected path: " + " -> ".join(path),
                "estimated_seconds_to_impact": None,
            })

    candidate_nodes = set(root_nodes)
    for rn in root_nodes:
        candidate_nodes.update(graph.neighbors(rn))
    for session in graph.bgp_sessions:
        if ("bgp_session", session["id"]) in symptomatic:
            continue
        if candidate_nodes & {session["a"], session["b"]}:
            entries.append({
                "anchor_type": "bgp_session", "anchor_id": session["id"],
                "node_id": session["a"],
                "hops_from_root": 0,
                "at_risk_reason": "BGP session on/adjacent to the root cause",
                "estimated_seconds_to_impact": None,
            })

    entries.sort(key=lambda e: (e["hops_from_root"], -graph.centrality(e["node_id"])))
    return entries


def _build_incident(
    group: list[Symptom], graph: Graph, settings: Settings,
    node_impact_estimates: dict[str, float | None],
) -> Incident:
    root, top, runner_up, rationale = _score_root_cause(group, graph, settings)
    if len(group) == 1:
        confidence = 1.0
    elif top + runner_up:
        confidence = top / (top + runner_up)
    else:
        confidence = 0.5
    severity_rank = max(SEVERITY_RANK.get(s.severity_max, 0) for s in group)
    cascade = _predict_cascade(root, group, graph, settings, node_impact_estimates)
    return Incident(
        incident_id=_incident_id(group),
        opened_at=min(s.first_seen for s in group),
        updated_at=max(s.last_seen for s in group),
        status="active",
        severity=rank_to_severity(severity_rank),
        root_cause={
            "anchor_type": root.anchor_type,
            "anchor_id": root.anchor_id,
            "node_id": root.node_id,
            "confidence": round(confidence, 3),
            "rationale": rationale,
        },
        symptoms=sorted(group, key=lambda s: s.first_seen),
        cascade=cascade,
    )


def correlate(
    symptoms: list[Symptom], graph: Graph, settings: Settings,
    node_impact_estimates: dict[str, float | None] | None = None,
) -> list[Incident]:
    active = [s for s in symptoms if s.severity_weight >= settings.min_symptom_weight]
    if not active:
        return []
    incidents = [
        _build_incident(group, graph, settings, node_impact_estimates or {})
        for group in _cluster(active, settings.temporal_window_seconds,
                              graph, settings.cascade_max_hops)
    ]
    incidents.sort(key=lambda i: i.opened_at, reverse=True)
    return incidents
