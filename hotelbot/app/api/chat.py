"""Demo/dev API: talk to the bot without WhatsApp.

POST /api/chat runs the exact same Orchestrator.handle() as the WhatsApp
webhook; only the channel name differs ("demo").
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_container, require_demo
from app.container import Container
from app.observability import log_event
from app.schemas.messages import AgentReply, InboundMessage

router = APIRouter(prefix="/api", tags=["demo"], dependencies=[Depends(require_demo)])


class ChatIn(BaseModel):
    guest_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    display_name: str | None = Field(default=None, max_length=200)


@router.post("/chat", response_model=AgentReply)
def chat(body: ChatIn, container: Container = Depends(get_container)) -> AgentReply:
    reply = container.orchestrator.handle(
        InboundMessage(channel="demo", sender_id=body.guest_id, text=body.message,
                       display_name=body.display_name)
    )
    if reply.text:
        log_event("message_sent", channel="demo", conversation_id=reply.conversation_id, via="api_response")
    return reply


@router.get("/dev/outbox")
def outbox(to: str | None = None, container: Container = Depends(get_container)) -> list[dict]:
    """Messages delivered through the mock transport (staff replies to demo
    guests, mock WhatsApp sends)."""
    return container.dev_outbox.outbox(to)
