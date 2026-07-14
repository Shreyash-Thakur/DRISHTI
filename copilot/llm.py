"""Thin, model-agnostic Ollama /api/chat client. Fully offline (local server
only), low-temperature, thinking suppressed. Never raises to the caller — any
failure yields ChatResult(available=False) so the orchestration layer can
degrade gracefully."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ChatResult:
    content: str
    model: str
    available: bool


class OllamaClient:
    def __init__(
        self, base_url: str, model: str, num_predict: int, num_ctx: int,
        temperature: float, client: httpx.AsyncClient,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_predict = num_predict
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.client = client

    async def chat(self, system: str, user: str) -> ChatResult:
        payload = {
            "model": self.model,
            "stream": False,
            "think": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {
                "num_predict": self.num_predict,
                "num_ctx": self.num_ctx,
                "temperature": self.temperature,
            },
        }
        try:
            response = await self.client.post(
                f"{self.base_url}/api/chat", json=payload,
                timeout=httpx.Timeout(180.0, connect=5.0),
            )
            response.raise_for_status()
            data = response.json()
            content = (data.get("message") or {}).get("content", "").strip()
            return ChatResult(content=content, model=self.model, available=True)
        except Exception:
            logger.warning("Ollama chat failed at %s (model %s)", self.base_url, self.model, exc_info=True)
            return ChatResult(content="", model=self.model, available=False)
