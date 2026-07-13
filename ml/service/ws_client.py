"""WS client to backend's /ws/live: feeds LiveState and triggers inference on
every incoming batch, with exponential-backoff reconnect on disconnect."""
from __future__ import annotations

import asyncio
import json
import logging

import pandas as pd
import websockets

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 30


async def _handle_message(raw: str, state, predictor, broadcast) -> None:
    message = json.loads(raw)
    if message.get("type") != "telemetry":
        return
    batch = message["batch"]
    touched: set[tuple[str, str]] = set()
    for m in batch.get("interface_metrics", []):
        row = {**m, "ts": pd.Timestamp(m["ts"])}
        state.add_metric(row)
        touched.add((row["node_id"], row["interface"]))
    for e in batch.get("events", []):
        row = {**e, "ts": pd.Timestamp(e["ts"])}
        state.add_event(row)

    for node_id, interface in touched:
        metric_history = state.metric_frame(node_id, interface)
        event_history = state.event_frame(node_id)
        as_of = metric_history["ts"].max()
        prediction = predictor.predict(node_id, interface, metric_history, event_history, as_of)
        state.set_prediction(node_id, interface, prediction)
        await broadcast.publish(prediction)


async def run_ws_client(ws_url: str, state, predictor, broadcast) -> None:
    backoff = 1
    while True:
        try:
            async with websockets.connect(ws_url) as ws:
                logger.info("connected to %s", ws_url)
                backoff = 1
                async for raw in ws:
                    await _handle_message(raw, state, predictor, broadcast)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("ws connection to %s lost, retrying in %ss", ws_url, backoff, exc_info=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
