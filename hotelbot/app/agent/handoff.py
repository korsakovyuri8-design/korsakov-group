"""Human handoff: build the structured package staff receive and switch the
conversation into human-owned mode.

While a conversation is HANDED_OFF the bot stays silent (guest messages are
still stored and visible to staff), except for emergencies. Staff return the
conversation to the bot by resolving the handoff.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.agent.memory import known_facts
from app.agent.policies import HandoffDecision
from app.db.models import Conversation, ConversationStatus, HumanHandoff, Urgency
from app.db.repositories import ActionRepository, ConversationRepository, HandoffRepository
from app.observability import log_event

_URGENCY_ORDER = [Urgency.LOW, Urgency.NORMAL, Urgency.HIGH, Urgency.CRITICAL]


def build_package(
    session: Session,
    conv: Conversation,
    decision: HandoffDecision,
    guest_request: str,
    intent: str,
    context_messages: int,
) -> dict[str, Any]:
    convs = ConversationRepository(session)
    messages = convs.recent_messages(conv, context_messages)
    stay = conv.stay
    facts = known_facts(stay.facts or {}) if stay else {}
    open_requests = [
        {"id": a.id, "type": a.request_type.value, "action_type": a.action_type, "status": a.status.value,
         "summary": a.summary}
        for a in ActionRepository(session).list(conversation_id=conv.id)
    ]
    return {
        "guest_id": conv.guest.external_id,
        "channel": conv.channel,
        "conversation_id": conv.id,
        "property_id": conv.property_id,
        "stay_id": conv.stay_id,
        "stay_status": stay.status.value if stay else None,
        "booking_reference": stay.booking_reference if stay else None,
        "language": conv.language,
        "reason": decision.reason.value,
        "urgency": decision.urgency.value,
        "intent": intent,
        "guest_request": guest_request,
        "summary": summarize(conv, decision, guest_request, facts, open_requests, len(messages)),
        "guest_stated_facts": facts,
        "actions": open_requests,
        "hotel_requests": open_requests,  # Core v1 key
        "messages": [
            {"role": m.role.value, "text": m.text, "at": m.created_at.isoformat() if m.created_at else None}
            for m in messages
        ],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def summarize(conv: Conversation, decision: HandoffDecision, guest_request: str,
              facts: dict[str, Any], requests: list[dict[str, Any]], n_messages: int) -> str:
    """Deterministic one-paragraph summary for staff (no LLM: it must not
    misstate what the guest said)."""
    parts = [
        f"Handoff ({decision.reason.value}, {decision.urgency.value}) for guest {conv.guest.external_id} "
        f"via {conv.channel}, language {conv.language or 'unknown'}.",
        f'Latest message: "{guest_request}".',
    ]
    if facts:
        stated = ", ".join(f"{k}={v['value']}" for k, v in facts.items())
        parts.append(f"Guest-stated (unverified): {stated}.")
    if requests:
        parts.append("Actions in this conversation: " + "; ".join(f"{r['action_type']} [{r['status']}]" for r in requests) + ".")
    parts.append(f"{n_messages} recent messages attached.")
    return " ".join(parts)


def create_handoff(
    session: Session,
    conv: Conversation,
    decision: HandoffDecision,
    guest_request: str,
    intent: str,
    context_messages: int = 10,
) -> HumanHandoff:
    """Open a handoff, or escalate urgency of the already-open one."""
    repo = HandoffRepository(session)
    package = build_package(session, conv, decision, guest_request, intent, context_messages)
    existing = repo.open_for(conv)
    if existing is not None:
        if _URGENCY_ORDER.index(decision.urgency) > _URGENCY_ORDER.index(existing.urgency):
            existing.urgency = decision.urgency
        existing.package = {**package, "previous_reason": existing.reason}
        handoff = existing
    else:
        handoff = repo.create(conv, decision.reason.value, decision.urgency, package)
    conv.status = ConversationStatus.HANDED_OFF
    session.flush()
    log_event(
        "handoff_created",
        handoff_id=handoff.id,
        conversation_id=conv.id,
        reason=decision.reason.value,
        urgency=handoff.urgency.value,
        reused=existing is not None,
    )
    return handoff
