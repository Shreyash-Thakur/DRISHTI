# copilot/ — Offline LLM Copilot (Phase 4, placeholder)

This package will hold the Ollama (Mistral 7B) integration and the RAG
pipeline over runbooks/incident history (ChromaDB).

Planned shape:

- `copilot/llm.py` — thin client for the local Ollama server (no outbound calls)
- `copilot/rag.py` — ChromaDB retrieval over `data/runbooks/`
- `copilot/explain.py` — takes a correlated incident (from the Phase-3 graph
  engine) and produces an operator-readable root-cause narrative

Context sources already available today:

- `GET /events` and `GET /metrics/{node_id}` on the backend (port 8000)
- direct import of `backend/app/services/telemetry_service.py`
