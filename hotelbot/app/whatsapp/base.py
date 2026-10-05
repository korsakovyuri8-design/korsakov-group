from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class SendResult(BaseModel):
    ok: bool
    message_id: str | None = None
    error: str | None = None


class MessageTransport(Protocol):
    """Outbound delivery to a guest on a messaging channel."""

    name: str

    def send_text(self, to: str, text: str) -> SendResult: ...
