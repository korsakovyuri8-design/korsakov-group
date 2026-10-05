"""In-memory WhatsApp transport for local development and tests.

Messages "sent" to guests are kept in an outbox that can be inspected via
GET /api/dev/outbox, so the full webhook -> agent -> send loop can be
exercised without Meta credentials.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from app.whatsapp.base import SendResult


class MockWhatsAppTransport:
    name = "mock"

    def __init__(self, max_items: int = 500) -> None:
        self._lock = threading.Lock()
        self._outbox: list[dict] = []
        self._max = max_items

    def send_text(self, to: str, text: str) -> SendResult:
        message_id = f"wamid.mock.{uuid.uuid4().hex[:16]}"
        with self._lock:
            self._outbox.append(
                {"id": message_id, "to": to, "text": text, "at": datetime.now(timezone.utc).isoformat()}
            )
            del self._outbox[: -self._max]
        return SendResult(ok=True, message_id=message_id)

    def outbox(self, to: str | None = None) -> list[dict]:
        with self._lock:
            return [m for m in self._outbox if to is None or m["to"] == to]

    def clear(self) -> None:
        with self._lock:
            self._outbox.clear()
