"""HTTP + WS routes for the ml service. Reads shared state off `request.app.state`
/ `ws.app.state`, set up in ml/service/app.py's lifespan."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "drishti-ml"}


@router.get("/predictions")
async def all_predictions(request: Request) -> list[dict]:
    return request.app.state.live_state.all_predictions()


@router.get("/predictions/{node_id}")
async def node_predictions(node_id: str, request: Request) -> list[dict]:
    results = request.app.state.live_state.predictions_for_node(node_id)
    if not results:
        raise HTTPException(status_code=404, detail=f"no predictions for node_id={node_id!r} yet")
    return results


@router.websocket("/ws/predictions")
async def ws_predictions(ws: WebSocket) -> None:
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
