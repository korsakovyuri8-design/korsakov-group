"""Meta WhatsApp Cloud API webhook.

GET  /webhooks/whatsapp  - subscription verification (hub.challenge echo)
POST /webhooks/whatsapp  - inbound messages; signature-checked, acknowledged
                           immediately, processed in a background task.
"""

from __future__ import annotations

import hmac
import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from pydantic import ValidationError

from app.agent import messages as msg
from app.api.deps import get_container
from app.container import Container
from app.observability import log_event
from app.schemas.messages import InboundMessage
from app.whatsapp.meta import ParsedInbound, WAWebhook, parse_webhook, verify_signature

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
    for item in inbound:
        background.add_task(process_inbound, container, item)
    # Meta retries non-2xx responses, so acknowledge fast; processing is async.
    return {"status": "accepted", "messages": len(inbound)}


def process_inbound(container: Container, item: ParsedInbound) -> None:
    transport = container.transport_for("whatsapp")
    try:
        runtime = container.properties.for_whatsapp_number(item.phone_number_id)
        if item.text is None:
            reply_text = msg.t("unsupported_media", "en")
        else:
            reply = container.orchestrator.handle(
                InboundMessage(channel="whatsapp", sender_id=item.sender_id, text=item.text,
                               external_id=item.message_id, display_name=item.display_name,
                               property_slug=runtime.slug)
            )
            reply_text = reply.text
        if not reply_text:
            return
        result = transport.send_text(item.sender_id, reply_text)
        log_event("message_sent", channel="whatsapp", transport=transport.name, ok=result.ok,
                  message_id=result.message_id, error=result.error)
    except Exception as exc:  # never let one message kill the worker
        log_event("error", where="whatsapp_process", error=type(exc).__name__, detail=str(exc)[:200])
