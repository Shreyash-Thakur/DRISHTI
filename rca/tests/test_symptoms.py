from datetime import datetime, timedelta, timezone

from rca.symptoms import (
    Symptom,
    anchor_for_event,
    build_symptoms,
    enrich_with_predictions,
)

BASE = datetime(2026, 7, 14, tzinfo=timezone.utc)


def _ev(node, sev, etype="syslog", ts=BASE, details=None, msg="m"):
    return {"ts": ts.isoformat(), "node_id": node, "severity": sev,
            "event_type": etype, "message": msg, "details": details or {}}


def test_anchor_for_event_bgp_session_and_node():
    assert anchor_for_event(_ev("pe-east", "warning", "bgp",
                                details={"session_id": "bgp-pe-east__pe-west"})) == (
        "bgp_session", "bgp-pe-east__pe-west")
    assert anchor_for_event(_ev("p-core-1", "error", "syslog")) == ("node", "p-core-1")


def test_build_symptoms_ignores_info_and_groups_by_anchor():
    events = [
        _ev("p-core-1", "info"),        # ignored
        _ev("p-core-1", "warning"),
        _ev("p-core-1", "error"),
    ]
    symptoms = build_symptoms(events, as_of=BASE, decay_tau=120.0)
    assert len(symptoms) == 1
    s = symptoms[0]
    assert s.anchor_type == "node" and s.anchor_id == "p-core-1"
    assert s.event_count == 2          # info dropped
    assert s.severity_max == "error"


def test_build_symptoms_drops_unknown_nodes():
    events = [_ev("ghost-node", "critical")]
    assert build_symptoms(events, as_of=BASE, decay_tau=120.0,
                          valid_nodes={"p-core-1"}) == []


def test_severity_weight_decays_old_critical_below_fresh_warning():
    fresh_warning = build_symptoms([_ev("pe-east", "warning", ts=BASE)],
                                   as_of=BASE, decay_tau=120.0)[0]
    old = BASE - timedelta(seconds=300)
    old_critical = build_symptoms([_ev("pe-west", "critical", ts=old)],
                                  as_of=BASE, decay_tau=120.0)[0]
    assert old_critical.severity_weight < fresh_warning.severity_weight


def test_enrich_with_predictions_folds_into_matching_node():
    symptoms = build_symptoms([_ev("pe-east", "warning")], as_of=BASE, decay_tau=120.0)
    enrich_with_predictions(symptoms, [
        {"node_id": "pe-east", "interface": "eth0",
         "precursor_probability": 0.8, "estimated_seconds_to_impact": 42.0},
    ])
    assert symptoms[0].precursor_probability == 0.8
    assert symptoms[0].estimated_seconds_to_impact == 42.0
