"""Staff API (MVP, no UI): inspect conversations, work the request queue,
take over / hand back conversations, and reply to guests."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_container, get_session, require_staff
from app.container import Container
from app.db.models import (
    Conversation,
    ConversationStatus,
    HandoffStatus,
    MessageRole,
    RequestStatus,
    utcnow,
)
from app.db.repositories import ConversationRepository, HandoffRepository, RequestRepository
from app.observability import log_event
from app.schemas.conversations import (
    ConversationOut,
    ConversationSummaryOut,
    HandoffOut,
    HotelRequestOut,
    MessageOut,
    ResolveHandoffIn,
    ResolveRequestIn,
    StaffMessageIn,
)

router = APIRouter(prefix="/api/staff", tags=["staff"], dependencies=[Depends(require_staff)])


def _summary(c: Conversation) -> ConversationSummaryOut:
    return ConversationSummaryOut(
        id=c.id, guest_external_id=c.guest.external_id, channel=c.channel, language=c.language,
        status=c.status, created_at=c.created_at, updated_at=c.updated_at,
    )


def _conversation_or_404(session: Session, conversation_id: str) -> Conversation:
    conv = ConversationRepository(session).get(conversation_id)
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    return conv


def _send_staff_message(session: Session, container: Container, conv: Conversation, text: str,
                        staff_name: str | None) -> MessageOut:
    message = ConversationRepository(session).add_message(
        conv, MessageRole.STAFF, text, language=conv.language, extra={"staff_name": staff_name}
    )
    transport = container.transport_for(conv.channel)
    result = transport.send_text(conv.guest.external_id, text)
    message.extra = {**message.extra, "delivery": result.model_dump()}
    log_event("message_sent", channel=conv.channel, transport=transport.name, role="staff",
              conversation_id=conv.id, ok=result.ok, error=result.error)
    if not result.ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"delivery failed: {result.error}")
    return MessageOut.model_validate(message)


# ----------------------------------------------------------- conversations
@router.get("/conversations", response_model=list[ConversationSummaryOut])
def list_conversations(conv_status: ConversationStatus | None = None, limit: int = 100,
                       session: Session = Depends(get_session)) -> list[ConversationSummaryOut]:
    return [_summary(c) for c in ConversationRepository(session).list(conv_status, min(limit, 500))]


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
def get_conversation(conversation_id: str, session: Session = Depends(get_session)) -> ConversationOut:
    conv = _conversation_or_404(session, conversation_id)
    return ConversationOut(
        **_summary(conv).model_dump(),
        memory={k: v for k, v in (conv.memory or {}).items()},
        messages=[MessageOut.model_validate(m) for m in conv.messages],
    )


@router.post("/conversations/{conversation_id}/messages", response_model=MessageOut,
             status_code=status.HTTP_201_CREATED)
def send_message(conversation_id: str, body: StaffMessageIn, session: Session = Depends(get_session),
                 container: Container = Depends(get_container)) -> MessageOut:
    conv = _conversation_or_404(session, conversation_id)
    return _send_staff_message(session, container, conv, body.text, body.staff_name)


# ---------------------------------------------------------------- requests
@router.get("/requests", response_model=list[HotelRequestOut])
def list_requests(request_status: RequestStatus | None = RequestStatus.PENDING,
                  session: Session = Depends(get_session)) -> list[HotelRequestOut]:
    return [HotelRequestOut.model_validate(r) for r in RequestRepository(session).list(request_status)]


@router.post("/requests/{request_id}/resolve", response_model=HotelRequestOut)
def resolve_request(request_id: str, body: ResolveRequestIn, session: Session = Depends(get_session),
                    container: Container = Depends(get_container)) -> HotelRequestOut:
    repo = RequestRepository(session)
    req = repo.get(request_id)
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "request not found")
    repo.resolve(req, body.status, body.note)
    log_event("request_updated", request_id=req.id, status=req.status.value)
    if body.reply_to_guest:
        conv = _conversation_or_404(session, req.conversation_id)
        _send_staff_message(session, container, conv, body.reply_to_guest, None)
    return HotelRequestOut.model_validate(req)


# ---------------------------------------------------------------- handoffs
@router.get("/handoffs", response_model=list[HandoffOut])
def list_handoffs(handoff_status: HandoffStatus | None = None,
                  session: Session = Depends(get_session)) -> list[HandoffOut]:
    return [HandoffOut.model_validate(h) for h in HandoffRepository(session).list(handoff_status)]


@router.post("/handoffs/{handoff_id}/accept", response_model=HandoffOut)
def accept_handoff(handoff_id: str, session: Session = Depends(get_session)) -> HandoffOut:
    handoff = HandoffRepository(session).get(handoff_id)
    if handoff is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "handoff not found")
    if handoff.status == HandoffStatus.OPEN:
        handoff.status = HandoffStatus.ACCEPTED
    return HandoffOut.model_validate(handoff)


@router.post("/handoffs/{handoff_id}/resolve", response_model=HandoffOut)
def resolve_handoff(handoff_id: str, body: ResolveHandoffIn, session: Session = Depends(get_session)) -> HandoffOut:
    """Close the handoff and give the conversation back to the bot (or close it)."""
    handoff = HandoffRepository(session).get(handoff_id)
    if handoff is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "handoff not found")
    handoff.status = HandoffStatus.RESOLVED
    handoff.resolved_at = utcnow()
    conv = _conversation_or_404(session, handoff.conversation_id)
    conv.status = ConversationStatus.CLOSED if body.close_conversation else ConversationStatus.ACTIVE
    conv.consecutive_failures = 0
    log_event("handoff_resolved", handoff_id=handoff.id, conversation_id=conv.id, conversation_status=conv.status.value)
    return HandoffOut.model_validate(handoff)
