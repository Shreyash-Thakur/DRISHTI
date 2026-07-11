"""DRISHTI backend — telemetry ingest, topology, timeseries queries, live WS feed.

Run (from repo root):  uvicorn app.main:app --app-dir backend --port 8000
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.repository.db import init_db
from app.routers import events, metrics, telemetry, topology, ws

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(get_settings().db_path)
    yield


app = FastAPI(
    title="DRISHTI Backend",
    description="Air-gapped predictive NOC copilot — Phase 1 telemetry API",
    version="0.1.0",
    lifespan=lifespan,
)

# Frontend (Phase 6) will run on a different port; everything is on a closed network.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(telemetry.router)
app.include_router(topology.router)
app.include_router(metrics.router)
app.include_router(events.router)
app.include_router(ws.router)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "drishti-backend"}
