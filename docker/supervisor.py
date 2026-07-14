"""PID-1 process supervisor for the all-in-one DRISHTI image.

Runs all five services + the static dashboard in ONE container. They share the
container's network namespace, so every service reaches the others on
localhost:<port> (which is also each service's built-in default) — no service
discovery needed.

Behaviour:
  * backend starts first; we wait for its /health before starting the rest, so
    the dependents' first connection attempts succeed instead of noisily
    retrying.
  * each child's stdout/stderr is line-prefixed with its name and streamed to
    the container log (unbuffered).
  * SIGTERM/SIGINT (i.e. `docker stop`) tears every child down cleanly.
  * if the backend (the one hard dependency) dies, the whole container exits
    non-zero so the orchestrator notices. A non-critical child dying is logged
    but does not take the container down — the demo stays up.

This is deliberately dependency-free (stdlib only) to keep the image lean.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request

APP_DIR = "/app"

# (name, argv, critical)
SERVICES = [
    ("backend",   ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000",
                   "--log-level", "warning"], True),
    ("simulator", [sys.executable, "-m", "sim.main"], False),
    ("ml",        [sys.executable, "-m", "ml.main"], False),
    ("rca",       [sys.executable, "-m", "rca.main"], False),
    ("copilot",   [sys.executable, "-m", "copilot.main"], False),
    ("frontend",  [sys.executable, "-m", "http.server", "8080",
                   "--directory", f"{APP_DIR}/frontend"], False),
]

_procs: dict[str, subprocess.Popen] = {}
_critical: set[str] = {name for name, _cmd, crit in SERVICES if crit}
_shutting_down = threading.Event()


def _log(msg: str) -> None:
    print(f"[supervisor] {msg}", flush=True)


def _pump(name: str, proc: subprocess.Popen) -> None:
    """Prefix and forward a child's merged output to our stdout."""
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(f"[{name}] {line}")
        sys.stdout.flush()


def _spawn(name: str, argv: list[str]) -> subprocess.Popen:
    proc = subprocess.Popen(
        argv, cwd=APP_DIR,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    _procs[name] = proc
    threading.Thread(target=_pump, args=(name, proc), daemon=True).start()
    _log(f"started {name} (pid {proc.pid})")
    return proc


def _wait_for_backend(timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _shutting_down.is_set():
            return False
        try:
            with urllib.request.urlopen("http://localhost:8000/health", timeout=2.0) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def _shutdown(exit_code: int) -> None:
    if _shutting_down.is_set():
        return
    _shutting_down.set()
    _log("shutting down — terminating children")
    for name, proc in _procs.items():
        if proc.poll() is None:
            proc.terminate()
    deadline = time.monotonic() + 10.0
    for name, proc in _procs.items():
        remaining = max(0.0, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _log(f"{name} did not exit in time — killing")
            proc.kill()
    _log(f"exit {exit_code}")
    os._exit(exit_code)


def _handle_signal(signum, _frame) -> None:
    _log(f"received signal {signum}")
    _shutdown(0)


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    _log("bringing up DRISHTI (all-in-one)")
    _spawn("backend", SERVICES[0][1])
    if not _wait_for_backend():
        _log("backend never became healthy — aborting")
        _shutdown(1)
    _log("backend healthy — starting dependents")
    for name, argv, _crit in SERVICES[1:]:
        _spawn(name, argv)

    # Supervise: react to any child exiting.
    while not _shutting_down.is_set():
        for name, proc in list(_procs.items()):
            code = proc.poll()
            if code is None:
                continue
            if name in _critical:
                _log(f"CRITICAL service {name!r} exited (code {code}) — taking the container down")
                _shutdown(1 if code else 0)
            else:
                _log(f"service {name!r} exited (code {code}) — continuing without it")
                _procs.pop(name, None)
        time.sleep(1.0)


if __name__ == "__main__":
    main()
