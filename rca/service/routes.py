"""HTTP + WS routes for the rca service. Reads shared state off request.app.state
/ ws.app.state, set up in rca/service/app.py's lifespan."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "drishti-rca"}


@router.post("/admin/reset")
async def reset(request: Request) -> dict:
    """Flush the rolling event buffer + incidents so a demo can restart cleanly
    without waiting out symptom decay. Pair with the simulator's DELETE /faults."""
    return {"cleared": request.app.state.rca_state.clear()}


@router.get("/incidents")
async def all_incidents(request: Request) -> list[dict]:
    incidents = request.app.state.rca_state.all_incidents()
    # active first, then newest-opened first
    return sorted(
        incidents,
        key=lambda i: (i.get("status") != "active", i.get("opened_at", "")),
    )


@router.get("/incidents/{incident_id}")
async def one_incident(incident_id: str, request: Request) -> dict:
    incident = request.app.state.rca_state.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"no incident with id={incident_id!r}")
    return incident


@router.websocket("/ws/incidents")
async def ws_incidents(ws: WebSocket) -> None:
    await ws.accept()
    broadcaster = ws.app.state.broadcaster
    await broadcaster.register(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.unregister(ws)
