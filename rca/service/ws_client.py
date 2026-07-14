"""WS client to backend's /ws/live: buffers events, best-effort enriches with ml
predictions, and runs a correlation pass on every incoming batch. Exponential
backoff reconnect on disconnect (mirrors ml/service/ws_client.py)."""
from __future__ import annotations

import asyncio
import json
import logging

import websockets

from rca.symptoms import parse_ts

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 30


async def _handle_message(raw: str, state, correlator, fetch_predictions) -> None:
    message = json.loads(raw)
    if message.get("type") != "telemetry":
        return
    batch = message["batch"]
    now = parse_ts(batch["sent_at"])
    for event in batch.get("events", []):
        state.add_event(event, now)
    predictions = await fetch_predictions()
    if predictions is not None:
        state.predictions = predictions
    await correlator.run_pass(now)


async def run_ws_client(ws_url: str, state, correlator, fetch_predictions) -> None:
    backoff = 1
    while True:
        try:
            async with websockets.connect(ws_url) as ws:
                logger.info("connected to %s", ws_url)
                backoff = 1
                async for raw in ws:
                    await _handle_message(raw, state, correlator, fetch_predictions)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("ws connection to %s lost, retrying in %ss", ws_url, backoff, exc_info=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
