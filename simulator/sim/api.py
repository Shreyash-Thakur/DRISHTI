"""Simulator control-plane: fault injection API + the telemetry loop that
generates and publishes a batch every tick."""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from sim.config import get_settings
from sim.events import BackgroundEvents
from sim.faults import SCENARIOS, FaultEngine
from sim.generator import MetricsGenerator
from sim.publisher import Publisher
from sim.topology import load_topology

logger = logging.getLogger(__name__)


class FaultRequest(BaseModel):
    scenario: str
    node_id: str
    interface: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


async def _telemetry_loop(
    generator: MetricsGenerator,
    engine: FaultEngine,
    background: BackgroundEvents,
    publisher: Publisher,
    interval: float,
) -> None:
    while True:
        now = datetime.now(timezone.utc)
        interface_metrics, tunnel_metrics = generator.tick(now, engine)
        events = background.tick(now) + engine.tick_events(now)
        ok = await publisher.send({
            "source": "drishti-simulator",
            "sent_at": now.isoformat(),
            "interface_metrics": interface_metrics,
            "tunnel_metrics": tunnel_metrics,
            "events": events,
        })
        if ok:
            logger.info(
                "Published %d interface samples, %d tunnel samples, %d events",
                len(interface_metrics), len(tunnel_metrics), len(events),
            )
        await asyncio.sleep(interval)


def create_app() -> FastAPI:
    settings = get_settings()
    topology = load_topology(settings.topology_path)
    engine = FaultEngine(topology)
    generator = MetricsGenerator(topology, seed=settings.seed)
    background = BackgroundEvents(topology, seed=settings.seed)
    publisher = Publisher(settings.backend_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(_telemetry_loop(
            generator, engine, background, publisher, settings.interval_seconds
        ))
        yield
        task.cancel()
        await publisher.close()

    app = FastAPI(
        title="DRISHTI Telemetry Simulator",
        description="Synthetic MPLS/SD-WAN telemetry with fault injection",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "drishti-simulator"}

    @app.get("/scenarios", tags=["faults"])
    def list_scenarios() -> dict[str, Any]:
        return {
            name: {"description": s["description"], "default_params": s["defaults"]}
            for name, s in SCENARIOS.items()
        }

    @app.get("/faults", tags=["faults"])
    def list_faults() -> list[dict[str, Any]]:
        return [f.describe() for f in engine.active()]

    @app.post("/faults", status_code=201, tags=["faults"])
    def inject_fault(req: FaultRequest) -> dict[str, Any]:
        try:
            fault = engine.inject(
                req.scenario, req.node_id, datetime.now(timezone.utc),
                interface=req.interface, params=req.params,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return fault.describe()

    @app.delete("/faults/{fault_id}", tags=["faults"])
    def clear_fault(fault_id: str) -> dict[str, Any]:
        fault = engine.clear(fault_id)
        if fault is None:
            raise HTTPException(status_code=404, detail=f"no active fault '{fault_id}'")
        return {"cleared": fault.describe()}

    @app.delete("/faults", tags=["faults"])
    def clear_all_faults() -> dict[str, int]:
        return {"cleared": engine.clear_all()}

    return app
