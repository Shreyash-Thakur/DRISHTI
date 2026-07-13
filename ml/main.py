"""Entrypoint: `python -m ml.main` (or `uvicorn ml.service.app:app --port 8200`
from the ml/ directory)."""
from __future__ import annotations

import uvicorn

from ml.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run("ml.service.app:app", host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
