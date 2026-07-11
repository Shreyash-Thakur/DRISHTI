from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.live_broadcaster import broadcaster

router = APIRouter(tags=["live"])


@router.websocket("/ws/live")
async def live_feed(ws: WebSocket) -> None:
    await broadcaster.register(ws)
    try:
        while True:
            # Clients don't need to send anything; this keeps the connection
            # open and lets us notice disconnects promptly.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.unregister(ws)
