from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from rca.service.broadcaster import Broadcaster
from rca.service.routes import router
from rca.service.state import RcaState


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.rca_state = RcaState(retention=timedelta(seconds=300))
    app.state.broadcaster = Broadcaster()
    return TestClient(app)


def test_health():
    response = _client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "drishti-rca"}


def test_incidents_empty_list_initially():
    response = _client().get("/incidents")
    assert response.status_code == 200
    assert response.json() == []


def test_incident_404_for_unknown_id():
    assert _client().get("/incidents/nope").status_code == 404


def test_incident_returns_seeded():
    client = _client()
    client.app.state.rca_state.set_incidents([{"incident_id": "inc-1", "status": "active"}])
    response = client.get("/incidents/inc-1")
    assert response.status_code == 200
    assert response.json()["incident_id"] == "inc-1"


def test_ws_incidents_receives_broadcast():
    # `anyio.from_thread.run(...)` can't be used here — this test thread isn't an
    # AnyIO worker thread. The live portal bound to the route handler's event loop
    # is on the WebSocketTestSession itself (`ws.portal`), so publish through it.
    client = _client()
    with client.websocket_connect("/ws/incidents") as ws:

        async def _publish():
            await client.app.state.broadcaster.publish({"incident_id": "inc-9"})

        ws.portal.call(_publish)
        message = ws.receive_json()
        assert message == {"type": "incident", "incident": {"incident_id": "inc-9"}}
