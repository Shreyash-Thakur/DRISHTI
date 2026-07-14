from copilot.prompt import build_messages


def _incident():
    return {
        "incident_id": "inc-1", "severity": "warning",
        "root_cause": {"anchor_type": "node", "anchor_id": "p-core-1",
                       "node_id": "p-core-1", "confidence": 1.0,
                       "rationale": ["central topology element"]},
        "symptoms": [{"anchor_type": "node", "anchor_id": "p-core-1", "node_id": "p-core-1",
                      "severity_max": "warning", "scenario": "link_degradation",
                      "sample_messages": ["%LINK-3-ERRORS: CRC/input errors increasing"]}],
        "cascade": [{"anchor_type": "tunnel", "anchor_id": "ipsec-a-to-b",
                     "hops_from_root": 0, "at_risk_reason": "rides the affected path"}],
    }


def test_build_messages_includes_root_cause_scenario_and_runbooks():
    snippets = [{"runbook": "link-degradation", "heading": "Symptoms", "text": "CRC errors...", "score": 0.9}]
    system, user = build_messages(_incident(), snippets)
    assert "DRISHTI" in system
    assert "p-core-1" in user
    assert "link_degradation" in user
    assert "ipsec-a-to-b" in user
    assert "link-degradation" in user and "CRC errors" in user


def test_build_messages_handles_no_snippets():
    system, user = build_messages(_incident(), [])
    assert "p-core-1" in user
