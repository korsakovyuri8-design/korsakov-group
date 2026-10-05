"""Thin data-access layer. Keeps SQL out of the agent and API modules."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Conversation,
    ConversationStatus,
    Guest,
    HandoffStatus,
    Hotel,
    HotelRequest,
    HumanHandoff,
    Message,
    MessageRole,
    RequestStatus,
    RequestType,
    Urgency,
    utcnow,
)


class HotelRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def get_by_slug(self, slug: str) -> Hotel | None:
        return self.s.scalar(select(Hotel).where(Hotel.slug == slug))


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

    def get_open_conversation(self, hotel_id: str, guest: Guest, channel: str) -> Conversation:
        """Latest non-closed conversation for the guest, or a new one."""
        conv = self.s.scalar(
            select(Conversation)
            .where(
                Conversation.hotel_id == hotel_id,
                Conversation.guest_id == guest.id,
                Conversation.status != ConversationStatus.CLOSED,
            )
            .order_by(Conversation.created_at.desc())
            .limit(1)
        )
        if conv is None:
            conv = Conversation(hotel_id=hotel_id, guest_id=guest.id, channel=channel, memory={})
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


class RequestRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def create(
        self,
        conv: Conversation,
        request_type: RequestType,
        summary: str,
        urgency: Urgency = Urgency.NORMAL,
        details: dict[str, Any] | None = None,
    ) -> HotelRequest:
        req = HotelRequest(
            conversation_id=conv.id,
            request_type=request_type,
            summary=summary,
            urgency=urgency,
            details=details or {},
        )
        self.s.add(req)
        self.s.flush()
        return req

    def get(self, request_id: str) -> HotelRequest | None:
        return self.s.get(HotelRequest, request_id)

    def list(self, status: RequestStatus | None = None, conversation_id: str | None = None) -> list[HotelRequest]:
        q = select(HotelRequest).order_by(HotelRequest.created_at.desc())
        if status:
            q = q.where(HotelRequest.status == status)
        if conversation_id:
            q = q.where(HotelRequest.conversation_id == conversation_id)
        return list(self.s.scalars(q))

    def resolve(self, req: HotelRequest, status: RequestStatus, note: str | None) -> HotelRequest:
        req.status = status
        req.resolution_note = note
        if status in (RequestStatus.RESOLVED, RequestStatus.REJECTED):
            req.resolved_at = utcnow()
        self.s.flush()
        return req


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
