"""Thin data-access layer. Keeps SQL out of the agent and API modules."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Action,
    ActionStatus,
    Conversation,
    ConversationStatus,
    Guest,
    HandoffStatus,
    HumanHandoff,
    Message,
    MessageRole,
    Property,
    Stay,
    StayStatus,
    Urgency,
    utcnow,
)


class PropertyRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def get_by_slug(self, slug: str) -> Property | None:
        return self.s.scalar(select(Property).where(Property.slug == slug))

    def get(self, property_id: str) -> Property | None:
        return self.s.get(Property, property_id)

    def list(self) -> list[Property]:
        return list(self.s.scalars(select(Property).order_by(Property.slug)))


HotelRepository = PropertyRepository  # Core v1 name


class ConversationRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def get_or_create_guest(self, channel: str, external_id: str, display_name: str | None = None) -> Guest:
        guest = self.s.scalar(
            select(Guest).where(Guest.channel == channel, Guest.external_id == external_id)
        )
        if guest is None:
            guest = Guest(channel=channel, external_id=external_id, display_name=display_name)
            self.s.add(guest)
            self.s.flush()
        elif display_name and not guest.display_name:
            guest.display_name = display_name
        return guest

    def get_open_conversation(self, stay: Stay, channel: str) -> Conversation:
        """Latest non-closed conversation of this stay, or a new one."""
        conv = self.s.scalar(
            select(Conversation)
            .where(
                Conversation.stay_id == stay.id,
                Conversation.status != ConversationStatus.CLOSED,
            )
            .order_by(Conversation.created_at.desc())
            .limit(1)
        )
        if conv is None:
            conv = Conversation(property_id=stay.property_id, guest_id=stay.guest_id, stay_id=stay.id,
                                channel=channel, memory={})
            self.s.add(conv)
            self.s.flush()
        return conv

    def get(self, conversation_id: str) -> Conversation | None:
        return self.s.get(Conversation, conversation_id)

    def list(self, status: ConversationStatus | None = None, limit: int = 100) -> list[Conversation]:
        q = select(Conversation).order_by(Conversation.updated_at.desc()).limit(limit)
        if status:
            q = q.where(Conversation.status == status)
        return list(self.s.scalars(q))

    def message_exists(self, external_id: str) -> bool:
        return self.s.scalar(select(Message.id).where(Message.external_id == external_id)) is not None

    def add_message(
        self,
        conv: Conversation,
        role: MessageRole,
        text: str,
        *,
        language: str | None = None,
        intent: str | None = None,
        external_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Message:
        seq = (
            self.s.scalar(select(func.max(Message.seq)).where(Message.conversation_id == conv.id)) or 0
        ) + 1
        msg = Message(
            conversation_id=conv.id,
            seq=seq,
            role=role,
            text=text,
            language=language,
            intent=intent,
            external_id=external_id,
            extra=extra or {},
        )
        self.s.add(msg)
        conv.updated_at = utcnow()
        self.s.flush()
        return msg

    def recent_messages(self, conv: Conversation, limit: int) -> list[Message]:
        rows = self.s.scalars(
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.seq.desc())
            .limit(limit)
        )
        return list(reversed(list(rows)))


class ActionRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def get(self, action_id: str) -> Action | None:
        return self.s.get(Action, action_id)

    def list(
        self,
        statuses: list[ActionStatus] | None = None,
        conversation_id: str | None = None,
        stay_id: str | None = None,
        property_id: str | None = None,
    ) -> list[Action]:
        q = select(Action).order_by(Action.created_at.desc())
        if statuses:
            q = q.where(Action.status.in_(statuses))
        if conversation_id:
            q = q.where(Action.conversation_id == conversation_id)
        if stay_id:
            q = q.where(Action.stay_id == stay_id)
        if property_id:
            q = q.where(Action.property_id == property_id)
        return list(self.s.scalars(q))


class StayRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def get(self, stay_id: str) -> Stay | None:
        return self.s.get(Stay, stay_id)

    def list(self, property_id: str | None = None, status: StayStatus | None = None,
             guest_id: str | None = None) -> list[Stay]:
        q = select(Stay).order_by(Stay.updated_at.desc())
        if property_id:
            q = q.where(Stay.property_id == property_id)
        if status:
            q = q.where(Stay.status == status)
        if guest_id:
            q = q.where(Stay.guest_id == guest_id)
        return list(self.s.scalars(q))


class HandoffRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def create(self, conv: Conversation, reason: str, urgency: Urgency, package: dict[str, Any]) -> HumanHandoff:
        handoff = HumanHandoff(conversation_id=conv.id, reason=reason, urgency=urgency, package=package)
        self.s.add(handoff)
        self.s.flush()
        return handoff

    def open_for(self, conv: Conversation) -> HumanHandoff | None:
        return self.s.scalar(
            select(HumanHandoff).where(
                HumanHandoff.conversation_id == conv.id,
                HumanHandoff.status != HandoffStatus.RESOLVED,
            )
        )

    def get(self, handoff_id: str) -> HumanHandoff | None:
        return self.s.get(HumanHandoff, handoff_id)

    def list(self, status: HandoffStatus | None = None) -> list[HumanHandoff]:
        q = select(HumanHandoff).order_by(HumanHandoff.created_at.desc())
        if status:
            q = q.where(HumanHandoff.status == status)
        return list(self.s.scalars(q))
