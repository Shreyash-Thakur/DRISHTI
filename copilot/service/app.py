"""FastAPI app: builds the runbook retriever + Ollama client at startup, exposes
the explain routes. LLM/rca reachability is handled per-request, not at startup —
the service comes up even if Ollama is down."""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from copilot.config import get_settings
from copilot.llm import OllamaClient
from copilot.rag import Retriever
from copilot.service.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.http = httpx.AsyncClient()
    app.state.retriever = Retriever.from_dir(settings.runbooks_dir)
    app.state.llm = OllamaClient(
        settings.ollama_url, settings.model, settings.num_predict,
        settings.num_ctx, settings.temperature, app.state.http)
    try:
        yield
    finally:
        await app.state.http.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="drishti-copilot", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
