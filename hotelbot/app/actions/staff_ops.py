"""Staff-side action operations shared by the staff API and the evaluation
harness: move an action through its lifecycle and tell the guest - with the
status template for the *new stored state*, never free text from the bot."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.actions.executors import ExecutorRegistry
from app.actions.service import ActionService
from app.agent.authority import status_message
from app.db.models import Action, ActionStatus, Conversation, MessageRole, Property
from app.db.repositories import ConversationRepository
from app.observability import log_event
from app.whatsapp.base import MessageTransport, SendResult


@dataclass
class TransitionResult:
    action: Action
    notification: str | None = None
    delivery: SendResult | None = None


def transition_action(
    session: Session,
    executors: ExecutorRegistry,
    action: Action,
    to: ActionStatus,
    actor: str,
    note: str | None = None,
    *,
    notify_guest: bool = True,
    transport_for=None,  # Callable[[str], MessageTransport]
) -> TransitionResult:
    ActionService(session, executors).transition(action, to, actor, note)
    result = TransitionResult(action=action)
    if not notify_guest or not action.conversation_id or transport_for is None:
        return result
    conv = session.get(Conversation, action.conversation_id)
    prop = session.get(Property, action.property_id)
    if conv is None or prop is None:
        return result
    text = status_message(action, conv.language or prop.default_language, prop.name)
    ConversationRepository(session).add_message(
        conv, MessageRole.BOT, text, language=conv.language,
        extra={"action_id": action.id, "action_status": action.status.value, "kind": "status_notification"},
    )
    transport: MessageTransport = transport_for(conv.channel)
    result.notification = text
    result.delivery = transport.send_text(conv.guest.external_id, text)
    log_event("message_sent", channel=conv.channel, transport=transport.name, role="bot",
              kind="status_notification", action_id=action.id, ok=result.delivery.ok)
    return result
