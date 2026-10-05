"""Staff API (MVP, no UI): inspect conversations, work the action queue,
verify stays, take over / hand back conversations, and reply to guests.

/api/staff/requests is the Core v1 view of the action queue, kept for
compatibility; /api/staff/actions is the Stay Engine API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_container, get_session, require_staff
from app.container import Container
from app.actions.service import InvalidTransition
from app.actions.staff_ops import transition_action
from sqlalchemy import select

from app.db.models import (
    Action,
    ActionStatus,
    ExternalTransaction,
    Job,
    JobStatus,
    Quote,
    Conversation,
    ConversationStatus,
    HandoffStatus,
    MessageRole,
    RequestStatus,
    StayStatus,
    utcnow,
)
from app.db.repositories import (
    ActionRepository,
    ConversationRepository,
    HandoffRepository,
    PropertyRepository,
    StayRepository,
)
from app.observability import log_event
from app.schemas.conversations import (
    ActionDetailOut,
    ActionOut,
    ActionTransitionIn,
    ActionTransitionOut,
    ConversationOut,
    ConversationSummaryOut,
    HandoffOut,
    HotelRequestOut,
    JobOut,
    MessageOut,
    PropertyOut,
    QuoteOut,
    ResolveHandoffIn,
    ResolveRequestIn,
    StaffMessageIn,
    StayOut,
    StayUpdateIn,
    TransactionOut,
)

# Core v1 request status <-> action status
_REQUEST_FILTER = {
    RequestStatus.PENDING: [ActionStatus.SUBMITTED],
    RequestStatus.IN_PROGRESS: [ActionStatus.ACCEPTED, ActionStatus.IN_PROGRESS],
    RequestStatus.RESOLVED: [ActionStatus.COMPLETED],
    RequestStatus.REJECTED: [ActionStatus.REJECTED, ActionStatus.FAILED, ActionStatus.CANCELLED],
}
_REQUEST_TARGET = {
    RequestStatus.IN_PROGRESS: ActionStatus.IN_PROGRESS,
    RequestStatus.RESOLVED: ActionStatus.COMPLETED,
    RequestStatus.REJECTED: ActionStatus.REJECTED,
}

router = APIRouter(prefix="/api/staff", tags=["staff"], dependencies=[Depends(require_staff)])


def _summary(c: Conversation) -> ConversationSummaryOut:
    return ConversationSummaryOut(
        id=c.id, guest_external_id=c.guest.external_id, property_id=c.property_id, stay_id=c.stay_id,
        channel=c.channel, language=c.language,
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


# ------------------------------------------------- requests (Core v1 view)
def _request_out(a: Action) -> HotelRequestOut:
    return HotelRequestOut(
        id=a.id, conversation_id=a.conversation_id or "", request_type=a.request_type.value,
        status=a.request_status.value, urgency=a.urgency.value, summary=a.summary, details=a.params,
        resolution_note=a.note, created_at=a.created_at, resolved_at=a.closed_at,
    )


@router.get("/requests", response_model=list[HotelRequestOut])
def list_requests(request_status: RequestStatus | None = RequestStatus.PENDING,
                  session: Session = Depends(get_session)) -> list[HotelRequestOut]:
    statuses = _REQUEST_FILTER[request_status] if request_status else None
    return [_request_out(a) for a in ActionRepository(session).list(statuses)]


@router.post("/requests/{request_id}/resolve", response_model=HotelRequestOut)
def resolve_request(request_id: str, body: ResolveRequestIn, session: Session = Depends(get_session),
                    container: Container = Depends(get_container)) -> HotelRequestOut:
    action = _action_or_404(session, request_id)
    target = _REQUEST_TARGET.get(body.status)
    if target is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "cannot move a request back to pending")
    _transition(session, container, action, target, "staff", body.note, notify_guest=False)
    log_event("request_updated", request_id=action.id, status=action.status.value)
    if body.reply_to_guest and action.conversation_id:
        conv = _conversation_or_404(session, action.conversation_id)
        _send_staff_message(session, container, conv, body.reply_to_guest, None)
    return _request_out(action)


# ----------------------------------------------------------------- actions
def _action_or_404(session: Session, action_id: str) -> Action:
    action = ActionRepository(session).get(action_id)
    if action is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "action not found")
    return action


def _transition(session: Session, container: Container, action: Action, to: ActionStatus, actor: str,
                note: str | None, *, notify_guest: bool):
    try:
        return transition_action(session, container.executors, action, to, actor, note,
                                 notify_guest=notify_guest, transport_for=container.transport_for)
    except InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/actions", response_model=list[ActionOut])
def list_actions(action_status: ActionStatus | None = None, property: str | None = None,
                 session: Session = Depends(get_session)) -> list[ActionOut]:
    property_id = None
    if property:
        prop = PropertyRepository(session).get_by_slug(property)
        if prop is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "property not found")
        property_id = prop.id
    actions = ActionRepository(session).list([action_status] if action_status else None, property_id=property_id)
    return [ActionOut.model_validate(a) for a in actions]


@router.get("/actions/{action_id}", response_model=ActionDetailOut)
def get_action(action_id: str, session: Session = Depends(get_session)) -> ActionDetailOut:
    return ActionDetailOut.model_validate(_action_or_404(session, action_id))


@router.post("/actions/{action_id}/transition", response_model=ActionTransitionOut)
def transition(action_id: str, body: ActionTransitionIn, session: Session = Depends(get_session),
               container: Container = Depends(get_container)) -> ActionTransitionOut:
    """Move an action through its lifecycle. By default the guest is told the
    new state using the fixed status template (never free text)."""
    action = _action_or_404(session, action_id)
    actor = f"staff:{body.staff_name}" if body.staff_name else "staff"
    result = _transition(session, container, action, body.to, actor, body.note, notify_guest=body.notify_guest)
    if result.delivery is not None and not result.delivery.ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"status saved, delivery failed: {result.delivery.error}")
    return ActionTransitionOut(action=ActionDetailOut.model_validate(action), guest_notification=result.notification)


# ------------------------------------------------------------------- stays
def _stay_or_404(session: Session, stay_id: str):
    stay = StayRepository(session).get(stay_id)
    if stay is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "stay not found")
    return stay


@router.get("/stays", response_model=list[StayOut])
def list_stays(stay_status: StayStatus | None = None, property: str | None = None,
               session: Session = Depends(get_session)) -> list[StayOut]:
    property_id = None
    if property:
        prop = PropertyRepository(session).get_by_slug(property)
        property_id = prop.id if prop else "-"
    return [StayOut.model_validate(s) for s in StayRepository(session).list(property_id, stay_status)]


@router.get("/stays/{stay_id}", response_model=StayOut)
def get_stay(stay_id: str, session: Session = Depends(get_session)) -> StayOut:
    return StayOut.model_validate(_stay_or_404(session, stay_id))


@router.patch("/stays/{stay_id}", response_model=StayOut)
def update_stay(stay_id: str, body: StayUpdateIn, session: Session = Depends(get_session)) -> StayOut:
    """Record staff-verified booking data. Guest-stated facts stay untouched."""
    stay = _stay_or_404(session, stay_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(stay, field, value)
    log_event("stay_updated", stay_id=stay.id, fields=sorted(body.model_dump(exclude_unset=True)))
    return StayOut.model_validate(stay)


# -------------------------------------------------------------- properties
@router.get("/properties", response_model=list[PropertyOut])
def list_properties(session: Session = Depends(get_session),
                    container: Container = Depends(get_container)) -> list[PropertyOut]:
    out = []
    for p in PropertyRepository(session).list():
        runtime = container.runtime_for_property_id(p.id)
        out.append(PropertyOut(
            id=p.id, slug=p.slug, name=p.name, property_type=p.property_type.value, timezone=p.timezone,
            default_language=p.default_language, active=p.active, is_synthetic=p.is_synthetic,
            capabilities=runtime.capabilities.describe() if runtime else {},
        ))
    return out


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


# ----------------------------------------------------- external transactions
@router.get("/quotes", response_model=list[QuoteOut])
def list_quotes(limit: int = 100, session: Session = Depends(get_session)) -> list[QuoteOut]:
    rows = session.scalars(select(Quote).order_by(Quote.created_at.desc()).limit(min(limit, 500)))
    return [QuoteOut.model_validate({**q.__dict__, "amount": str(q.amount), "status": q.status.value}) for q in rows]


@router.get("/transactions", response_model=list[TransactionOut])
def list_transactions(limit: int = 100, session: Session = Depends(get_session)) -> list[TransactionOut]:
    rows = session.scalars(select(ExternalTransaction).order_by(ExternalTransaction.created_at.desc())
                           .limit(min(limit, 500)))
    return [TransactionOut(
        id=t.id, action_id=t.action_id, status=t.action.status, service_type=t.action.action_type,
        provider=t.provider.slug, quote_id=t.quote_id, idempotency_key=t.idempotency_key,
        provider_reference=t.provider_reference, request=t.request, last_error=t.last_error,
        created_at=t.created_at, submitted_at=t.submitted_at) for t in rows]


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(job_status: JobStatus | None = None, limit: int = 100,
              session: Session = Depends(get_session)) -> list[JobOut]:
    q = select(Job).order_by(Job.created_at.desc()).limit(min(limit, 500))
    if job_status:
        q = q.where(Job.status == job_status)
    return [JobOut.model_validate({**j.__dict__, "status": j.status.value}) for j in session.scalars(q)]


@router.get("/stays/{stay_id}/plan")
def stay_plan(stay_id: str, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """The guest's trip plan: each item with its own status (read from the
    linked quote/action where there is one)."""
    from app.trip.itinerary import plan

    return [{"id": e.item.id, "kind": e.item.kind, "title": e.item.title, "status": e.status,
             "starts_at": e.starts_at.isoformat() if e.starts_at else None, "quote_id": e.item.quote_id,
             "action_id": e.item.action_id, "place_id": e.item.place_id, "event_id": e.item.event_id}
            for e in plan(session, stay_id)]
