from datetime import datetime, timedelta, timezone

from rca.config import Settings
from rca.correlate import correlate, incident_signature
from rca.graph import Graph
from rca.symptoms import Symptom

BASE = datetime(2026, 7, 14, tzinfo=timezone.utc)
GRAPH = Graph.from_path("data/topology.json")
SETTINGS = Settings()


def _sym(anchor_type, anchor_id, node_id, offset_s, weight=5.0, sev="error"):
    t = BASE + timedelta(seconds=offset_s)
    return Symptom(anchor_type, anchor_id, node_id, t, t, sev, weight, 1)


def test_low_weight_symptoms_produce_no_incident():
    weak = _sym("node", "pe-east", "pe-east", 0, weight=0.0)
    assert correlate([weak], GRAPH, SETTINGS) == []


def test_single_symptom_incident_is_its_own_root_cause():
    incidents = correlate([_sym("node", "p-core-1", "p-core-1", 0)], GRAPH, SETTINGS)
    assert len(incidents) == 1
    rc = incidents[0].root_cause
    assert rc["anchor_id"] == "p-core-1"
    assert rc["confidence"] == 1.0


def test_earliest_central_symptom_wins_root_cause_and_tunnels_cascade():
    # core link degrades first, PE shows symptoms 30s later
    core = _sym("node", "p-core-1", "p-core-1", 0)
    pe = _sym("node", "pe-east", "pe-east", 30)
    incidents = correlate([pe, core], GRAPH, SETTINGS)
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.root_cause["node_id"] == "p-core-1"
    assert inc.root_cause["confidence"] > 0.5
    # the CE-to-CE IPsec tunnels ride through p-core-1 -> they're in the cascade
    tunnel_ids = {c["anchor_id"] for c in inc.cascade if c["anchor_type"] == "tunnel"}
    assert "ipsec-a-to-b" in tunnel_ids


def test_simultaneous_but_topologically_distant_symptoms_split():
    # two unrelated faults at (nearly) the same time on leaf nodes 4 hops apart
    # (> cascade_max_hops) must NOT be merged into one bogus incident
    a = _sym("node", "ce-site-a", "ce-site-a", 0)
    b = _sym("node", "ce-site-b", "ce-site-b", 2)
    incidents = correlate([a, b], GRAPH, SETTINGS)
    assert len(incidents) == 2
    assert {i.root_cause["node_id"] for i in incidents} == {"ce-site-a", "ce-site-b"}


def test_simultaneous_adjacent_symptoms_stay_one_incident():
    # topologically adjacent (1 hop) + temporally close -> a single incident
    core = _sym("node", "p-core-1", "p-core-1", 0)
    pe = _sym("node", "pe-east", "pe-east", 2)
    incidents = correlate([pe, core], GRAPH, SETTINGS)
    assert len(incidents) == 1


def test_temporally_distant_symptoms_split_into_two_incidents():
    a = _sym("node", "pe-east", "pe-east", 0)
    b = _sym("node", "pe-west", "pe-west", 10_000)  # far outside temporal window
    incidents = correlate([a, b], GRAPH, SETTINGS)
    assert len(incidents) == 2


def test_cascade_estimates_pulled_from_node_impact_estimates():
    core = _sym("node", "p-core-1", "p-core-1", 0)
    incidents = correlate([core], GRAPH, SETTINGS,
                          node_impact_estimates={"pe-east": 33.0})
    pe_entries = [c for c in incidents[0].cascade
                  if c["anchor_type"] == "node" and c["node_id"] == "pe-east"]
    assert pe_entries and pe_entries[0]["estimated_seconds_to_impact"] == 33.0


def test_incident_signature_is_stable_across_timestamp_only_changes():
    inc1 = correlate([_sym("node", "p-core-1", "p-core-1", 0)], GRAPH, SETTINGS)[0]
    inc2 = correlate([_sym("node", "p-core-1", "p-core-1", 5)], GRAPH, SETTINGS)[0]
    assert incident_signature(inc1.to_dict()) == incident_signature(inc2.to_dict())
