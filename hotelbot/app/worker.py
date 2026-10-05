"""Standalone job worker (run several for throughput; safe on PostgreSQL).

    python -m app.worker
"""

from __future__ import annotations

import signal
import sys
import threading

from app.config import get_settings
from app.container import build_container
from app.observability import configure_logging, log_event


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    container = build_container(settings)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    log_event("worker_process_started", worker_id=container.worker.worker_id)
    while not stop.is_set():
        if not container.worker.run_once():
            stop.wait(settings.worker_poll_seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
