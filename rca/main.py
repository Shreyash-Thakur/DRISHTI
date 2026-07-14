"""Entrypoint: `python -m rca.main` (or `uvicorn rca.service.app:app --port 8300`)."""
from __future__ import annotations

import uvicorn

from rca.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run("rca.service.app:app", host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
