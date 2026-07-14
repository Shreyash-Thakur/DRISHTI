"""Entrypoint: `python -m copilot.main` (or `uvicorn copilot.service.app:app --port 8400`)."""
from __future__ import annotations

import uvicorn

from copilot.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run("copilot.service.app:app", host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
