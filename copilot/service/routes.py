"""HTTP routes for the copilot service. Reads shared objects off request.app.state
(set up in copilot/service/app.py's lifespan)."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from copilot.explain import explain

router = APIRouter()

_REQUIRED_KEYS = ("incident_id", "root_cause", "symptoms", "cascade")


class ExplainRequest(BaseModel):
    incident: dict | None = None
    incident_id: str | None = None


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "drishti-copilot"}


async def _fetch_incident(http: httpx.AsyncClient, rca_url: str, incident_id: str) -> dict:
    try:
        response = await http.get(f"{rca_url.rstrip('/')}/incidents/{incident_id}", timeout=5.0)
    except Exception as exc:
        raise HTTPException(502, f"could not reach rca at {rca_url}: {exc}") from exc
    if response.status_code == 404:
        raise HTTPException(404, f"rca has no incident {incident_id!r}")
    if response.status_code >= 400:
        raise HTTPException(502, f"rca returned {response.status_code} for {incident_id!r}")
    return response.json()


@router.post("/explain")
async def explain_route(req: ExplainRequest, request: Request) -> dict:
    state = request.app.state
    incident = req.incident
    if incident is None:
        if not req.incident_id:
            raise HTTPException(422, "provide 'incident' (full dict) or 'incident_id'")
        incident = await _fetch_incident(state.http, state.settings.rca_url, req.incident_id)
    missing = [k for k in _REQUIRED_KEYS if k not in incident]
    if missing:
        raise HTTPException(422, f"incident missing required keys: {missing}")
    return await explain(incident, state.retriever, state.llm, state.settings.top_k)
