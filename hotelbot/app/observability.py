"""Structured (JSON lines) logging.

Use `log_event(name, **fields)` for the domain events listed in the brief:
message_received, intent_detected, knowledge_retrieved, tool_called,
handoff_created, message_sent, error.

Field names that look like secrets are redacted defensively, and message
bodies are truncated so logs do not become a second conversation store.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

_LOGGER = logging.getLogger("hotelbot")
_SECRET_MARKERS = ("token", "secret", "password", "api_key", "authorization")
_MAX_TEXT = 200


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _LOGGER.handlers[:] = [handler]
    _LOGGER.setLevel(level.upper())
    _LOGGER.propagate = False


def _clean(key: str, value: Any) -> Any:
    if any(marker in key.lower() for marker in _SECRET_MARKERS):
        return "[redacted]"
    if isinstance(value, str) and len(value) > _MAX_TEXT:
        return value[:_MAX_TEXT] + "…"
    return value


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **{k: _clean(k, v) for k, v in fields.items()},
    }
    _LOGGER.log(level, json.dumps(record, default=str, ensure_ascii=False))
