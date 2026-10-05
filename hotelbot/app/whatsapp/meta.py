"""Meta WhatsApp Cloud API: webhook payload parsing, signature verification
and outbound sending.

Reference shape of an inbound webhook (abridged):

{"object": "whatsapp_business_account",
 "entry": [{"id": "...", "changes": [{"field": "messages", "value": {
     "messaging_product": "whatsapp",
     "metadata": {"display_phone_number": "...", "phone_number_id": "..."},
     "contacts": [{"profile": {"name": "Ana"}, "wa_id": "38267123456"}],
     "messages": [{"from": "38267123456", "id": "wamid.X", "timestamp": "...",
                   "type": "text", "text": {"body": "Hi"}}],
     "statuses": [...]}}]}]}
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.whatsapp.base import SendResult


# ------------------------------------------------------------- payload model
class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class WAText(_Lenient):
    body: str = Field(max_length=4096)


class WAMessage(_Lenient):
    from_: str = Field(alias="from", max_length=64)
    id: str = Field(max_length=255)
    timestamp: str | None = None
    type: str
    text: WAText | None = None


class WAProfile(_Lenient):
    name: str | None = None


class WAContact(_Lenient):
    wa_id: str
    profile: WAProfile | None = None


class WAMetadata(_Lenient):
    phone_number_id: str | None = None
    display_phone_number: str | None = None


class WAValue(_Lenient):
    messaging_product: str | None = None
    metadata: WAMetadata | None = None
    contacts: list[WAContact] = []
    messages: list[WAMessage] = []
    statuses: list[dict[str, Any]] = []


class WAChange(_Lenient):
    field: str
    value: WAValue


class WAEntry(_Lenient):
    id: str | None = None
    changes: list[WAChange] = []


class WAWebhook(_Lenient):
    object: str
    entry: list[WAEntry] = []


class ParsedInbound(BaseModel):
    sender_id: str
    message_id: str
    type: str
    text: str | None
    display_name: str | None
    phone_number_id: str | None = None  # receiving business number -> property routing


def parse_webhook(payload: WAWebhook) -> list[ParsedInbound]:
    """Flatten a webhook into guest messages. Status callbacks (delivered,
    read...) are ignored."""
    out: list[ParsedInbound] = []
    for entry in payload.entry:
        for change in entry.changes:
            if change.field != "messages":
                continue
            names = {c.wa_id: c.profile.name if c.profile else None for c in change.value.contacts}
            for m in change.value.messages:
                out.append(
                    ParsedInbound(
                        sender_id=m.from_,
                        message_id=m.id,
                        type=m.type,
                        text=m.text.body if m.type == "text" and m.text else None,
                        display_name=names.get(m.from_),
                        phone_number_id=change.value.metadata.phone_number_id if change.value.metadata else None,
                    )
                )
    return out


def verify_signature(app_secret: str, body: bytes, signature_header: str | None) -> bool:
    """Validate X-Hub-Signature-256 ("sha256=<hex HMAC of raw body>")."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


# ----------------------------------------------------------------- transport
class MetaWhatsAppTransport:
    name = "meta"

    def __init__(self, access_token: str, phone_number_id: str, graph_version: str, timeout: float,
                 client: httpx.Client | None = None) -> None:
        if not access_token or not phone_number_id:
            raise ValueError("WhatsApp access token and phone number id are required for the meta transport")
        self._url = f"https://graph.facebook.com/{graph_version}/{phone_number_id}/messages"
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._client = client or httpx.Client(timeout=timeout)

    def send_text(self, to: str, text: str) -> SendResult:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": text[:4096]},
        }
        try:
            resp = self._client.post(self._url, json=payload, headers=self._headers)
        except httpx.HTTPError as exc:
            return SendResult(ok=False, error=f"transport error: {type(exc).__name__}")
        if resp.status_code >= 300:
            return SendResult(ok=False, error=f"HTTP {resp.status_code}")
        try:
            message_id = resp.json()["messages"][0]["id"]
        except (ValueError, KeyError, IndexError, TypeError):
            message_id = None
        return SendResult(ok=True, message_id=message_id)
