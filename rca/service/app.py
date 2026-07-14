"""FastAPI app: builds the graph + state at startup, runs the backend WS client
as a background task (with best-effort ml enrichment), exposes incident routes."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from rca.config import get_settings
from rca.graph import Graph
from rca.service.broadcaster import Broadcaster
from rca.service.correlator import Correlator
from rca.service.routes import router
from rca.service.state import RcaState
from rca.service.ws_client import run_ws_client

logger = logging.getLogger(__name__)


def _make_fetch_predictions(client: httpx.AsyncClient, ml_url: str):
    async def fetch_predictions():
        try:
            response = await client.get(f"{ml_url}/predictions", timeout=2.0)
            response.raise_for_status()
            return response.json()
        except Exception:
            logger.debug("ml enrichment unavailable at %s", ml_url, exc_info=True)
            return None
    return fetch_predictions


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    retention = timedelta(
        seconds=settings.temporal_window_seconds + settings.decay_tau_seconds + 60)
    app.state.rca_state = RcaState(retention=retention)
    app.state.broadcaster = Broadcaster()
    graph = Graph.from_path(settings.topology_path)
    correlator = Correlator(graph, settings, app.state.rca_state, app.state.broadcaster)
    client = httpx.AsyncClient()
    fetch_predictions = _make_fetch_predictions(client, settings.ml_url)
    task = asyncio.create_task(
        run_ws_client(settings.backend_ws_url, app.state.rca_state, correlator, fetch_predictions))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="drishti-rca", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
