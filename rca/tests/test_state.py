from datetime import datetime, timedelta, timezone

from rca.service.state import RcaState

BASE = datetime(2026, 7, 14, tzinfo=timezone.utc)


def _ev(node, ts):
    return {"ts": ts.isoformat(), "node_id": node, "severity": "warning",
            "event_type": "syslog", "message": "m", "details": {}}


def test_add_event_prunes_beyond_retention():
    state = RcaState(retention=timedelta(seconds=100))
    state.add_event(_ev("pe-east", BASE), BASE)
    later = BASE + timedelta(seconds=101)
    state.add_event(_ev("pe-west", later), later)
    events = state.current_events()
    assert len(events) == 1
    assert events[0]["node_id"] == "pe-west"


def test_set_and_get_incidents_roundtrip():
    state = RcaState(retention=timedelta(seconds=100))
    assert state.all_incidents() == []
    inc = {"incident_id": "inc-1", "status": "active"}
    state.set_incidents([inc])
    assert state.all_incidents() == [inc]
    assert state.get_incident("inc-1") == inc
    assert state.get_incident("nope") is None
