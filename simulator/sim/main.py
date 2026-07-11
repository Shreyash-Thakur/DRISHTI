"""Simulator entrypoint.

Run (from repo root):  python -m sim.main   (with PYTHONPATH=simulator)
or:                    cd simulator && python -m sim.main
"""
import logging

import uvicorn

from sim.api import create_app
from sim.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def run() -> None:
    settings = get_settings()
    uvicorn.run(create_app(), host=settings.host, port=settings.api_port)


if __name__ == "__main__":
    run()
