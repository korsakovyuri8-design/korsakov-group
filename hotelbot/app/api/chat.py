"""Demo/dev API: talk to the bot without WhatsApp.

POST /api/chat runs the exact same Orchestrator.handle() as the WhatsApp
webhook; only the channel name differs ("demo").
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.agent.runtime import UnknownProperty
from app.api.deps import get_container, require_demo
from app.container import Container
from app.observability import log_event
from app.schemas.messages import AgentReply, InboundMessage

router = APIRouter(prefix="/api", tags=["demo"], dependencies=[Depends(require_demo)])


class ChatIn(BaseModel):
    guest_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    display_name: str | None = Field(default=None, max_length=200)
    # Property slug; omitted = the deployment's default property.
    property: str | None = Field(default=None, max_length=64)


@router.post("/chat", response_model=AgentReply)
def chat(body: ChatIn, background: BackgroundTasks, container: Container = Depends(get_container)) -> AgentReply:
    try:
        container.properties.get(body.property)
    except UnknownProperty:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown property {body.property!r}") from None
    reply = container.orchestrator.handle(
        InboundMessage(channel="demo", sender_id=body.guest_id, text=body.message,
                       display_name=body.display_name, property_slug=body.property)
    )
    if reply.text:
        log_event("message_sent", channel="demo", conversation_id=reply.conversation_id, via="api_response")
    # Provider submissions / notifications queued by this turn run right after
    # the response; their results reach the guest via /api/dev/outbox.
    background.add_task(container.kick)
    return reply


@router.get("/dev/outbox")
def outbox(to: str | None = None, container: Container = Depends(get_container)) -> list[dict]:
    """Messages delivered through the mock transport (staff replies to demo
    guests, mock WhatsApp sends)."""
    return container.dev_outbox.outbox(to)
