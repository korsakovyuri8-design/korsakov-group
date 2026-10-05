"""Signed provider callbacks.

POST /api/providers/{property_slug}/{provider_slug}/callbacks

    X-Provider-Timestamp: <unix seconds>
    X-Provider-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>

    {"event_id": "evt-123", "reference": "<provider booking reference>",
     "event": "accepted|rejected|in_progress|completed|failed|cancelled", "message": "..."}

Checks, in order (each failure leaves state untouched):
 1. provider exists, is active, and has a callback secret configured  (404 / 503)
 2. signature over timestamp+body, constant-time comparison           (401)
 3. timestamp within the replay window (default 300 s)                 (401)
 4. well-formed payload                                                (400)
 5. event_id not seen before for this provider -> otherwise "duplicate" (200, no-op)
 6. reference belongs to one of this provider's transactions           (404, logged)
 7. transition is legal in the Action state machine                    (409, logged)
    (already in that state -> 200 "already_in_state", no-op)
Then the transition is applied through TransactionService.apply_status,
which records the audit event and queues the guest notification.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import ActionStatus, ExternalProvider, ExternalTransaction, Property, ProviderEvent
from app.observability import log_event
from app.transactions.service import TransactionService, TxnDeps

REPLAY_WINDOW_SECONDS = 300
_EVENTS = {
    "accepted": ActionStatus.ACCEPTED, "rejected": ActionStatus.REJECTED, "in_progress": ActionStatus.IN_PROGRESS,
    "completed": ActionStatus.COMPLETED, "failed": ActionStatus.FAILED, "cancelled": ActionStatus.CANCELLED,
    # Conditional services (weather...): accepted subject to a condition, then
    # either confirmed unconditionally or called off.
    "conditional": ActionStatus.PENDING_CONDITION, "condition_met": ActionStatus.ACCEPTED,
    "condition_failed": ActionStatus.CANCELLED,
}


class CallbackPayload(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)
    reference: str = Field(min_length=1, max_length=255)
    event: str
    message: str | None = Field(default=None, max_length=2000)


@dataclass
class CallbackResult:
    status_code: int
    body: dict[str, Any]


def sign(secret: str, timestamp: int | str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def handle_callback(session: Session, deps: TxnDeps, property_slug: str, provider_slug: str,
                    headers: dict[str, str], body: bytes) -> CallbackResult:
    prop = session.scalar(select(Property).where(Property.slug == property_slug))
    provider = None
    if prop is not None:   # the property's own partner, or a shared provider of its region
        scope = ExternalProvider.property_id == prop.id
        if region := (prop.extra or {}).get("region"):
            scope = or_(scope, ExternalProvider.region == region)
        provider = session.scalar(select(ExternalProvider).where(ExternalProvider.slug == provider_slug, scope))
    if provider is None or not provider.active:
        return CallbackResult(404, {"detail": "unknown provider"})
    secret_env = (provider.config or {}).get("callback_secret_env")
    secret = os.environ.get(secret_env) if secret_env else None
    if not secret:
        log_event("error", where="provider_callback", error="callback secret not configured", provider=provider.slug)
        return CallbackResult(503, {"detail": "callbacks not configured for this provider"})

    lower = {k.lower(): v for k, v in headers.items()}
    timestamp, signature = lower.get("x-provider-timestamp"), lower.get("x-provider-signature")
    if not timestamp or not signature or not hmac.compare_digest(sign(secret, timestamp, body), signature):
        log_event("provider_callback_rejected", provider=provider.slug, reason="bad_signature")
        return CallbackResult(401, {"detail": "invalid signature"})
    try:
        age = abs(deps.clock.now().timestamp() - int(timestamp))
    except ValueError:
        return CallbackResult(401, {"detail": "invalid timestamp"})
    if age > REPLAY_WINDOW_SECONDS:
        log_event("provider_callback_rejected", provider=provider.slug, reason="stale_timestamp", age=int(age))
        return CallbackResult(401, {"detail": "timestamp outside replay window"})

    try:
        payload = CallbackPayload.model_validate(json.loads(body))
    except (ValueError, ValidationError):
        return CallbackResult(400, {"detail": "malformed payload"})
    target = _EVENTS.get(payload.event)
    if target is None:
        return CallbackResult(400, {"detail": f"unknown event {payload.event!r}"})

    if session.scalar(select(ProviderEvent.id).where(ProviderEvent.provider_id == provider.id,
                                                     ProviderEvent.event_id == payload.event_id)):
        log_event("provider_callback_duplicate", provider=provider.slug, event_id=payload.event_id)
        return CallbackResult(200, {"status": "duplicate"})

    txn = session.scalar(select(ExternalTransaction).where(ExternalTransaction.provider_id == provider.id,
                                                           ExternalTransaction.provider_reference == payload.reference))
    # Claim the event id BEFORE acting, so concurrent duplicates cannot both apply.
    event = ProviderEvent(provider_id=provider.id, event_id=payload.event_id, event_type=payload.event,
                          provider_reference=payload.reference, action_id=txn.action_id if txn else None,
                          outcome="received", payload=payload.model_dump(), received_at=deps.clock.now())
    try:
        with session.begin_nested():
            session.add(event)
            session.flush()
    except IntegrityError:
        return CallbackResult(200, {"status": "duplicate"})
    template = "txn_condition_failed" if payload.event == "condition_failed" else None
    event.outcome = "unknown_reference" if txn is None else TransactionService(session, deps).apply_status(
        txn, target, f"provider:{provider.slug}", payload.message, template)
    outcome = event.outcome
    log_event("provider_callback", provider=provider.slug, event_id=payload.event_id, event_type=payload.event,
              outcome=outcome)
    if outcome == "unknown_reference":
        return CallbackResult(404, {"status": outcome})
    if outcome == "invalid_transition":
        return CallbackResult(409, {"status": outcome, "current": txn.action.status.value if txn else None})
    return CallbackResult(200, {"status": outcome})
