"""Domain persistence model (Stay Engine).

    Guest ──< Stay >── Property ──< KnowledgeDocument
                │
                ├──< Conversation ──< Message
                │         └──< HumanHandoff
                └──< Action ──< ActionEvent

Portable SQLAlchemy 2.0 types only (JSON, string-backed enums) so the same
models run on PostgreSQL and SQLite. Schema changes go through Alembic
(migrations/), see docs/DECISIONS.md D-017+.

Core v1 names (Hotel, HotelKnowledgeDocument, HotelRequest, RequestStatus)
remain importable as compatibility aliases.
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
    return Enum(cls, native_enum=False, length=32, values_callable=lambda e: [m.value for m in e],
                name=cls.__name__.lower())


# ------------------------------------------------------------------ enums
class PropertyType(str, enum.Enum):
    HOTEL = "hotel"
    HOSTEL = "hostel"
    RESORT = "resort"
    VACATION_RENTAL = "vacation_rental"
    APARTMENT = "apartment"
    GLAMPING = "glamping"
    OTHER = "other"


class StayStatus(str, enum.Enum):
    INQUIRY = "inquiry"          # contact without a verified booking (the default)
    BOOKED = "booked"            # verified booking (staff / PMS)
    IN_HOUSE = "in_house"
    CHECKED_OUT = "checked_out"
    CANCELLED = "cancelled"


OPEN_STAY_STATUSES = (StayStatus.INQUIRY, StayStatus.BOOKED, StayStatus.IN_HOUSE)


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
    """Request *topics* recognised in guest language by the intent layer.
    Mapped to property capabilities (action types) in app/actions/catalog.py."""

    LATE_CHECK_IN = "late_check_in"
    EARLY_CHECK_IN = "early_check_in"
    LATE_CHECK_OUT = "late_check_out"
    HOUSEKEEPING = "housekeeping"
    MAINTENANCE = "maintenance"
    RESTAURANT = "restaurant"
    TRANSPORT = "transport"
    BOOKING = "booking"
    OTHER = "other"


class ActionStatus(str, enum.Enum):
    """Lifecycle of a real-world action. SUBMITTED != ACCEPTED != COMPLETED;
    only system/tool state moves an action between these (never model prose)."""

    PROPOSED = "proposed"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_ACTION_STATUSES = frozenset(
    {ActionStatus.REJECTED, ActionStatus.COMPLETED, ActionStatus.FAILED, ActionStatus.CANCELLED}
)


class RequestStatus(str, enum.Enum):
    """Core v1 request status, kept for the /api/staff/requests compatibility view."""

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


# ----------------------------------------------------------------- tables
class Property(Base):
    """Any accommodation provider: hotel, hostel, vacation rental, glamping..."""

    __tablename__ = "properties"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    property_type: Mapped[PropertyType] = mapped_column(_enum(PropertyType), default=PropertyType.HOTEL)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    default_language: Mapped[str] = mapped_column(String(16), default="en")
    active: Mapped[bool] = mapped_column(default=True)
    is_synthetic: Mapped[bool] = mapped_column(default=False)
    # Declared capability set (see app/capabilities). Empty = Core v1 defaults.
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # emergency_number, policy overrides, channel routing (whatsapp_phone_number_id) ...
    extra: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    documents: Mapped[list[KnowledgeDocument]] = relationship(
        back_populates="prop", cascade="all, delete-orphan"
    )


class KnowledgeDocument(Base):
    """One retrievable knowledge item from a property knowledge pack."""

    __tablename__ = "knowledge_documents"
    __table_args__ = (UniqueConstraint("property_id", "item_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    item_key: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(255))
    # {"en": "...", "cnr": "..."}; any locale may be added.
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    keywords: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    extra: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    prop: Mapped[Property] = relationship(back_populates="documents")

    @property
    def hotel_id(self) -> str:  # Core v1 compatibility
        return self.property_id


class Guest(Base):
    __tablename__ = "guests"
    __table_args__ = (UniqueConstraint("channel", "external_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    channel: Mapped[str] = mapped_column(String(32))          # "whatsapp", "demo", ...
    external_id: Mapped[str] = mapped_column(String(128))     # e.g. WhatsApp wa_id
    display_name: Mapped[str | None] = mapped_column(String(200))
    # Global, cross-stay preferences only (e.g. preferred language).
    # Stay-specific facts live on Stay.facts and never here.
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    conversations: Mapped[list[Conversation]] = relationship(back_populates="guest")
    stays: Mapped[list[Stay]] = relationship(back_populates="guest")


class Stay(Base):
    """The context that connects a Guest to a Property for one visit."""

    __tablename__ = "stays"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id"), index=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), index=True)
    status: Mapped[StayStatus] = mapped_column(_enum(StayStatus), default=StayStatus.INQUIRY)
    # Authoritative fields: set only by staff or an integration (PMS), never
    # from guest statements.
    booking_reference: Mapped[str | None] = mapped_column(String(128))
    arrival_at: Mapped[datetime | None] = mapped_column()
    departure_at: Mapped[datetime | None] = mapped_column()
    party_size: Mapped[int | None] = mapped_column(Integer)
    source_channel: Mapped[str | None] = mapped_column(String(32))
    # Guest-stated, unconfirmed facts for this stay:
    # {"guest_count": {"value": 2, "source": "guest_stated", "confirmed": false, ...}}
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    extra: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    guest: Mapped[Guest] = relationship(back_populates="stays")
    prop: Mapped[Property] = relationship()


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), index=True)
    guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id"), index=True)
    stay_id: Mapped[str | None] = mapped_column(ForeignKey("stays.id"), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    language: Mapped[str | None] = mapped_column(String(16))  # reply locale, e.g. "cnr-Cyrl"
    status: Mapped[ConversationStatus] = mapped_column(
        _enum(ConversationStatus), default=ConversationStatus.ACTIVE
    )
    # Dialogue state only (pending offers, last topic). Facts live on the Stay.
    memory: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    guest: Mapped[Guest] = relationship(back_populates="conversations")
    stay: Mapped[Stay | None] = relationship()
    prop: Mapped[Property] = relationship()
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", order_by="Message.created_at, Message.seq"
    )

    @property
    def hotel_id(self) -> str:  # Core v1 compatibility
        return self.property_id


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    seq: Mapped[int] = mapped_column(Integer, default=0)  # tiebreaker within a conversation
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[MessageRole] = mapped_column(_enum(MessageRole))
    text: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(16))
    intent: Mapped[str | None] = mapped_column(String(32))
    # Channel message id (e.g. WhatsApp wamid) - unique to make webhook retries idempotent.
    external_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    extra: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Action(Base):
    """A real-world action requested on the guest's behalf (housekeeping,
    late arrival, taxi...). Status changes only through ActionService."""

    __tablename__ = "actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), index=True)
    stay_id: Mapped[str | None] = mapped_column(ForeignKey("stays.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(64))       # capability key, e.g. "late_arrival_request"
    status: Mapped[ActionStatus] = mapped_column(_enum(ActionStatus), default=ActionStatus.PROPOSED)
    urgency: Mapped[Urgency] = mapped_column(_enum(Urgency), default=Urgency.NORMAL)
    summary: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    executor: Mapped[str] = mapped_column(String(64), default="staff")   # "staff" | "webhook" | ...
    external_ref: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column()

    events: Mapped[list[ActionEvent]] = relationship(
        back_populates="action", order_by="ActionEvent.seq", cascade="all, delete-orphan"
    )

    # ---- Core v1 HotelRequest compatibility -------------------------------
    @property
    def request_type(self) -> RequestType:
        from app.actions.catalog import request_type_for

        return request_type_for(self.action_type)

    @property
    def details(self) -> dict[str, Any]:
        return self.params

    @property
    def resolution_note(self) -> str | None:
        return self.note

    @property
    def resolved_at(self) -> datetime | None:
        return self.closed_at

    @property
    def request_status(self) -> RequestStatus:
        return {
            ActionStatus.PROPOSED: RequestStatus.PENDING,
            ActionStatus.SUBMITTED: RequestStatus.PENDING,
            ActionStatus.ACCEPTED: RequestStatus.IN_PROGRESS,
            ActionStatus.IN_PROGRESS: RequestStatus.IN_PROGRESS,
            ActionStatus.COMPLETED: RequestStatus.RESOLVED,
        }.get(self.status, RequestStatus.REJECTED)


class ActionEvent(Base):
    """Append-only audit trail of action status transitions."""

    __tablename__ = "action_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    action_id: Mapped[str] = mapped_column(ForeignKey("actions.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    from_status: Mapped[ActionStatus | None] = mapped_column(_enum(ActionStatus))
    to_status: Mapped[ActionStatus] = mapped_column(_enum(ActionStatus))
    actor: Mapped[str] = mapped_column(String(128))   # "agent", "staff:<name>", "executor:webhook", "system"
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    action: Mapped[Action] = relationship(back_populates="events")


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


# ---------------------------------------------------- Core v1 aliases
Hotel = Property
HotelKnowledgeDocument = KnowledgeDocument
HotelRequest = Action
