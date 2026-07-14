"""FastAPI app: loads models at startup (fatal if missing — see Predictor),
runs the backend WS client as a background task, exposes prediction routes."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ml.config import get_settings
from ml.service.broadcaster import Broadcaster
from ml.service.predictor import Predictor
from ml.service.routes import router
from ml.service.state import LiveState
from ml.service.ws_client import run_ws_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.live_state = LiveState()
    app.state.broadcaster = Broadcaster()
    app.state.predictor = Predictor(settings.model_dir, settings.precursor_threshold)
    task = asyncio.create_task(
        run_ws_client(settings.backend_ws_url, app.state.live_state, app.state.predictor, app.state.broadcaster)
    )
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def create_app() -> FastAPI:
    app = FastAPI(title="drishti-ml", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
