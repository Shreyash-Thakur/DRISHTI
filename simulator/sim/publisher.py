"""Ships telemetry batches to the backend over HTTP. Failures are logged and
dropped — the next tick generates fresh data anyway."""
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class Publisher:
    def __init__(self, backend_url: str) -> None:
        self._ingest_url = f"{backend_url.rstrip('/')}/telemetry/ingest"
        self._client = httpx.AsyncClient(timeout=10.0)

    async def send(self, batch: dict[str, Any]) -> bool:
        try:
            resp = await self._client.post(self._ingest_url, json=batch)
            resp.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            logger.warning("Failed to publish batch to %s: %s", self._ingest_url, exc)
            return False

    async def close(self) -> None:
        await self._client.aclose()
