"""Meta WhatsApp Cloud API webhook.

GET  /webhooks/whatsapp  - subscription verification (hub.challenge echo)
POST /webhooks/whatsapp  - inbound messages; signature-checked, persisted as
                           durable jobs, acknowledged, then processed by the
                           job worker (app/jobs/handlers.py: process_inbound).
"""

from __future__ import annotations

import hmac
import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from pydantic import ValidationError

from app.api.deps import get_container
from app.container import Container
from app.observability import log_event
from app.jobs.queue import enqueue
from app.whatsapp.meta import WAWebhook, parse_webhook, verify_signature

router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])

MAX_BODY_BYTES = 256 * 1024


@router.get("")
def verify_subscription(
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
    container: Container = Depends(get_container),
) -> Response:
    expected = container.settings.whatsapp_verify_token
    if (
        mode == "subscribe"
        and expected is not None
        and token is not None
        and challenge is not None
        and hmac.compare_digest(token, expected.get_secret_value())
    ):
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status.HTTP_403_FORBIDDEN, "verification failed")


@router.post("")
async def receive(
    request: Request, background: BackgroundTasks, container: Container = Depends(get_container)
) -> dict:
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    secret = container.settings.whatsapp_app_secret
    if secret is not None:
        if not verify_signature(secret.get_secret_value(), body, request.headers.get("X-Hub-Signature-256")):
            log_event("error", where="whatsapp_webhook", error="invalid_signature")
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid signature")
    elif container.settings.env == "prod":
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "webhook app secret not configured")

    try:
        payload = WAWebhook.model_validate(json.loads(body))
    except (ValueError, ValidationError):
        log_event("error", where="whatsapp_webhook", error="malformed_payload")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "malformed payload")
    if payload.object != "whatsapp_business_account":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unexpected object")

    inbound = parse_webhook(payload)
    # Durable before acknowledging: each message becomes a job in the same
    # commit, deduplicated on the WhatsApp message id (Meta retries).
    with container.session_factory() as session:
        for item in inbound:
            enqueue(session, "process_inbound", item.model_dump(), f"inbound:{item.message_id}",
                    clock=container.clock)
        session.commit()
    # Fast path: process right after responding. If this process dies first,
    # the background/standalone worker picks the jobs up.
    background.add_task(container.kick)
    return {"status": "accepted", "messages": len(inbound)}
