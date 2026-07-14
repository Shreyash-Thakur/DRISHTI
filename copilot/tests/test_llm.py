import httpx
import pytest

from copilot.llm import OllamaClient


def _client(handler) -> OllamaClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return OllamaClient("http://ollama", "mistral:7b", 256, 4096, 0.2, http)


@pytest.mark.anyio
async def test_chat_parses_message_content():
    def handler(request):
        assert request.url.path == "/api/chat"
        return httpx.Response(200, json={"message": {"content": "Root cause: p-core-1."}, "done": True})

    result = await _client(handler).chat("sys", "user")
    assert result.available is True
    assert result.content == "Root cause: p-core-1."
    assert result.model == "mistral:7b"


@pytest.mark.anyio
async def test_chat_unavailable_on_transport_error():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    result = await _client(handler).chat("sys", "user")
    assert result.available is False
    assert result.content == ""
