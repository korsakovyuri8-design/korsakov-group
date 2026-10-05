"""Domain persistence model.

Portable SQLAlchemy 2.0 types only (JSON, String enums) so the same models run
on PostgreSQL (Docker Compose / production) and SQLite (tests, quick hacking).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, datetime: DateTime(timezone=True)}


def _enum(cls: type[enum.Enum]) -> Enum:
    # Store enum values as plain strings (no native PG enum) to keep
    # migrations trivial while the vocabulary is still evolving.
    return Enum(cls, native_enum=False, length=32, values_callable=lambda e: [m.value for m in e])


class ConversationStatus(str, enum.Enum):
    ACTIVE = "active"            # bot is handling the guest
    HANDED_OFF = "handed_off"    # a human owns the conversation; bot stays quiet
    CLOSED = "closed"


class MessageRole(str, enum.Enum):
    GUEST = "guest"
    BOT = "bot"
    STAFF = "staff"
    SYSTEM = "system"


class RequestType(str, enum.Enum):
    LATE_CHECK_IN = "late_check_in"
    EARLY_CHECK_IN = "early_check_in"
    LATE_CHECK_OUT = "late_check_out"
    HOUSEKEEPING = "housekeeping"
    MAINTENANCE = "maintenance"
    RESTAURANT = "restaurant"
    TRANSPORT = "transport"
    BOOKING = "booking"
    OTHER = "other"


class RequestStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class Urgency(str, enum.Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class HandoffStatus(str, enum.Enum):
    OPEN = "open"
    ACCEPTED = "accepted"
    RESOLVED = "resolved"


class Hotel(Base):
    __tablename__ = "hotels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    is_synthetic: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    documents: Mapped[list[HotelKnowledgeDocument]] = relationship(
        back_populates="hotel", cascade="all, delete-orphan"
    )


class HotelKnowledgeDocument(Base):
    """One retrievable knowledge item from a hotel knowledge pack."""

    __tablename__ = "hotel_knowledge_documents"
    __table_args__ = (UniqueConstraint("hotel_id", "item_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    hotel_id: Mapped[str] = mapped_column(ForeignKey("hotels.id", ondelete="CASCADE"))
    item_key: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(255))
    # {"en": "...", "cnr": "..."}; any language code may be added later.
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    keywords: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    extra: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    hotel: Mapped[Hotel] = relationship(back_populates="documents")


class Guest(Base):
    __tablename__ = "guests"
    __table_args__ = (UniqueConstraint("channel", "external_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    channel: Mapped[str] = mapped_column(String(32))          # "whatsapp", "demo", ...
    external_id: Mapped[str] = mapped_column(String(128))     # e.g. WhatsApp wa_id
    display_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    conversations: Mapped[list[Conversation]] = relationship(back_populates="guest")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    hotel_id: Mapped[str] = mapped_column(ForeignKey("hotels.id"))
    guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id"), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    language: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[ConversationStatus] = mapped_column(
        _enum(ConversationStatus), default=ConversationStatus.ACTIVE
    )
    # Session memory. Each fact: {"value", "source", "confirmed", "updated_at"}.
    memory: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    guest: Mapped[Guest] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", order_by="Message.created_at, Message.seq"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    seq: Mapped[int] = mapped_column(Integer, default=0)  # tiebreaker within a conversation
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[MessageRole] = mapped_column(_enum(MessageRole))
    text: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(8))
    intent: Mapped[str | None] = mapped_column(String(32))
    # Channel message id (e.g. WhatsApp wamid) - unique to make webhook retries idempotent.
    external_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    extra: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class HotelRequest(Base):
    """An actionable request for hotel staff (the output of the action layer)."""

    __tablename__ = "hotel_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    request_type: Mapped[RequestType] = mapped_column(_enum(RequestType))
    status: Mapped[RequestStatus] = mapped_column(_enum(RequestStatus), default=RequestStatus.PENDING)
    urgency: Mapped[Urgency] = mapped_column(_enum(Urgency), default=Urgency.NORMAL)
    summary: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    resolution_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column()


class HumanHandoff(Base):
    __tablename__ = "human_handoffs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    reason: Mapped[str] = mapped_column(String(64))
    urgency: Mapped[Urgency] = mapped_column(_enum(Urgency), default=Urgency.NORMAL)
    status: Mapped[HandoffStatus] = mapped_column(_enum(HandoffStatus), default=HandoffStatus.OPEN)
    # The structured handoff package (see app/agent/handoff.py).
    package: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column()
