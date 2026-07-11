from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.schemas import IngestResult, TelemetryBatch
from app.services import telemetry_service
from app.services.live_broadcaster import broadcaster

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.post("/ingest", response_model=IngestResult)
async def ingest(batch: TelemetryBatch) -> IngestResult:
    settings = get_settings()
    try:
        result = await run_in_threadpool(
            telemetry_service.ingest_batch, settings.db_path, settings.topology_path, batch
        )
    except telemetry_service.UnknownNodeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await broadcaster.publish({"type": "telemetry", "batch": batch.model_dump(mode="json")})
    return result
