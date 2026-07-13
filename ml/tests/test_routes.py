from fastapi import FastAPI
from fastapi.testclient import TestClient

from ml.service.broadcaster import Broadcaster
from ml.service.routes import router
from ml.service.state import LiveState


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.live_state = LiveState()
    app.state.broadcaster = Broadcaster()
    return TestClient(app)


def test_health():
    response = _client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "drishti-ml"}


def test_predictions_empty_list_when_nothing_seen_yet():
    response = _client().get("/predictions")
    assert response.status_code == 200
    assert response.json() == []


def test_node_predictions_404_for_unknown_node():
    response = _client().get("/predictions/does-not-exist")
    assert response.status_code == 404


def test_node_predictions_returns_seeded_prediction():
    client = _client()
    client.app.state.live_state.set_prediction("pe-east", "eth0", {
        "node_id": "pe-east", "interface": "eth0",
        "precursor_probability": 0.9, "estimated_seconds_to_impact": 42.0,
    })
    response = client.get("/predictions/pe-east")
    assert response.status_code == 200
    assert response.json()[0]["precursor_probability"] == 0.9


def test_ws_predictions_receives_broadcast_message():
    # NOTE: `anyio.from_thread.run(...)` doesn't work here — this test's thread is
    # not an AnyIO worker thread, so there's no event-loop token for it to find
    # (raises `anyio.NoEventLoopError`). Starlette's `TestClient` doesn't expose a
    # usable `.portal` either unless it's entered as a context manager (`with
    # TestClient(app) as client:`), which this suite's `_client()` helper doesn't
    # do. What *does* carry a live portal is the `WebSocketTestSession` object
    # itself (the `ws` returned by `websocket_connect(...).__enter__()`) — it opens
    # its own `anyio.BlockingPortal` bound to the same event loop that's running the
    # `/ws/predictions` route handler, and stores it as `ws.portal`. Calling
    # `ws.portal.call(...)` runs `_publish` on that same loop/thread, so the
    # broadcaster's `asyncio.Lock` and the registered `WebSocket` are all touched
    # from one consistent event loop — exactly what's needed to publish while the
    # WS session is live.
    client = _client()
    with client.websocket_connect("/ws/predictions") as ws:

        async def _publish():
            await client.app.state.broadcaster.publish({"node_id": "pe-east", "interface": "eth0"})

        ws.portal.call(_publish)
        message = ws.receive_json()
        assert message == {"type": "prediction", "prediction": {"node_id": "pe-east", "interface": "eth0"}}
