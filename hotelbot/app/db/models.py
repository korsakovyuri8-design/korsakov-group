"""Domain persistence model (Stay Engine).

    Guest ──< Stay >── Property ──< KnowledgeDocument
                │          └──< ExternalProvider
                ├──< Conversation ──< Message
                │         └──< HumanHandoff
                ├──< Quote ──(guest consent)──> ExternalTransaction ── Action ──< ActionEvent
                └──< Action ──< ActionEvent

    ProviderEvent: signed provider callbacks (idempotency / replay log)
    Job: durable outbox / work queue (PostgreSQL, SKIP LOCKED)

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

from decimal import Decimal

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
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
    # We sent it, the provider's answer never arrived (timeout, crash): the
    # booking may or may not exist. Never reported as failed or booked;
    # resolved by reconciliation (provider lookup or staff).
    SUBMISSION_UNKNOWN = "submission_unknown"
    SUBMITTED = "submitted"
    # Provider accepted SUBJECT TO a condition (weather, minimum group...):
    # not a confirmed booking until the provider confirms unconditionally.
    PENDING_CONDITION = "pending_condition"
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


# ------------------------------------------- external service transactions
class QuoteStatus(str, enum.Enum):
    OFFERED = "offered"
    ACCEPTED_BY_GUEST = "accepted_by_guest"
    DECLINED_BY_GUEST = "declined_by_guest"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    DEAD = "dead"          # permanently failed or out of attempts


class ExternalProvider(Base):
    """A third party that fulfils services for a property (transfers,
    restaurants, rentals...). Configuration holds no secrets - only the
    *names* of environment variables that hold them."""

    __tablename__ = "external_providers"
    __table_args__ = (UniqueConstraint("property_id", "slug"), UniqueConstraint("region", "slug"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Scope: a property's own partner (property_id) or a regional provider
    # usable by any property in the region / by travellers without a property.
    property_id: Mapped[str | None] = mapped_column(ForeignKey("properties.id"), index=True)
    region: Mapped[str | None] = mapped_column(String(64), index=True)
    slug: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    provider_type: Mapped[str] = mapped_column(String(32))        # transport | restaurant | activities ...
    integration_type: Mapped[str] = mapped_column(String(32))     # mock | webhook
    active: Mapped[bool] = mapped_column(default=True)
    services: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)   # {"service_types": [...]}
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Marketplace profile: where it operates and its default policies
    # (cancellation, deposits, ID...). Offerings may override policies.
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)    # languages, service_area, location...
    policies: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Commercial metadata (never alters prices shown or consent).
    commission_type: Mapped[str | None] = mapped_column(String(32))      # percent | fixed | markup | none
    commission_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ProviderRelation(str, enum.Enum):
    PREFERRED = "preferred"     # ranked first among suitable providers
    DEFAULT = "default"         # used when the guest expresses no preference (ranked first)
    EXCLUSIVE = "exclusive"     # the only provider for the listed services
    BLOCKED = "blocked"         # never used for this property


class PropertyProvider(Base):
    """How one property relates to a (shared) provider. Providers are not
    owned by properties: one provider serves many properties."""

    __tablename__ = "property_providers"
    __table_args__ = (UniqueConstraint("property_id", "provider_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), index=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("external_providers.id"), index=True)
    relation: Mapped[ProviderRelation] = mapped_column(_enum(ProviderRelation))
    services: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)   # {"service_types": [...]} or {} = all
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Quote(Base):
    """A provider's priced offer. A quote is never a booking; only explicit
    guest consent to *this* quote creates a transaction."""

    __tablename__ = "quotes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(8), index=True)   # short id the guest can refer to
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), index=True)
    stay_id: Mapped[str] = mapped_column(ForeignKey("stays.id"), index=True)
    guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"))
    provider_id: Mapped[str] = mapped_column(ForeignKey("external_providers.id"))
    service_type: Mapped[str] = mapped_column(String(64))
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)   # the details that were priced
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    description: Mapped[str] = mapped_column(Text)
    conditions: Mapped[str | None] = mapped_column(Text)
    valid_until: Mapped[datetime] = mapped_column()
    provider_reference: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[QuoteStatus] = mapped_column(_enum(QuoteStatus), default=QuoteStatus.OFFERED)
    # Evidence of consent: message id, verbatim text, time.
    consent: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Material terms shown with the price (deposit, ID, age, cancellation...);
    # consent to the quote is consent to exactly these, recorded verbatim.
    terms: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    offering_id: Mapped[str | None] = mapped_column(ForeignKey("offerings.id"))
    # Snapshot for future accounting; never changes `amount` the guest saw.
    commercial: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # A change of a confirmed booking: this quote replaces that transaction.
    replaces_transaction_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column()


class ExternalTransaction(Base):
    """Provider-side details of an Action that commits a third party.
    Its status is the Action's status (one state machine)."""

    __tablename__ = "external_transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    action_id: Mapped[str] = mapped_column(ForeignKey("actions.id"), unique=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("external_providers.id"), index=True)
    quote_id: Mapped[str] = mapped_column(ForeignKey("quotes.id"), unique=True)   # one booking per quote
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    provider_reference: Mapped[str | None] = mapped_column(String(255), index=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    submit_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column()

    action: Mapped[Action] = relationship()
    provider: Mapped[ExternalProvider] = relationship()
    quote: Mapped[Quote] = relationship()


class ProviderEvent(Base):
    """Every provider callback received (accepted or not): replay and
    duplicate protection, plus an audit log."""

    __tablename__ = "provider_events"
    __table_args__ = (UniqueConstraint("provider_id", "event_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    provider_id: Mapped[str] = mapped_column(ForeignKey("external_providers.id"), index=True)
    event_id: Mapped[str] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(32))
    provider_reference: Mapped[str | None] = mapped_column(String(255))
    action_id: Mapped[str | None] = mapped_column(ForeignKey("actions.id"))
    outcome: Mapped[str] = mapped_column(String(32))   # applied | already_in_state | invalid_transition | unknown_reference
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    received_at: Mapped[datetime] = mapped_column(default=utcnow)


class Job(Base):
    """Durable unit of work (outbox). Written in the same DB transaction as
    the state change that requires it, so an acknowledged request can never
    lose its follow-up work."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_due", "status", "next_attempt_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus), default=JobStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=6)
    next_attempt_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column()


# ------------------------------------------- local travel infrastructure
class ItemStatus(str, enum.Enum):
    """Lightweight plan states for items that are NOT transactions. Items
    linked to a quote/action take their status from that stored state."""

    SAVED = "saved"
    SHORTLISTED = "shortlisted"
    PROPOSED = "proposed"
    DISMISSED = "dismissed"


class _Provenance:
    """Every external factual record carries where it came from and how
    fresh it is (dynamic facts - hours, prices, events - go stale)."""

    source: Mapped[str] = mapped_column(String(255), default="unknown")
    last_verified_at: Mapped[datetime | None] = mapped_column()
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    provider_owned: Mapped[bool] = mapped_column(default=False)   # supplied by the business itself
    is_synthetic: Mapped[bool] = mapped_column(default=False)


class Place(_Provenance, Base):
    """Anything a traveller can go to: restaurant, bar, museum, pharmacy, ATM,
    beach, bus stop... One table: `category` (top-level taxonomy) +
    `subcategory` (open vocabulary) + structured `attributes`. Adding a new
    kind of place is a data change, not a schema change."""

    __tablename__ = "places"
    __table_args__ = (UniqueConstraint("region", "slug"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    region: Mapped[str] = mapped_column(String(64), index=True)
    slug: Mapped[str] = mapped_column(String(96))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(32), index=True)
    subcategory: Mapped[str] = mapped_column(String(64), index=True)
    tags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)          # {"tags": [...]}
    description: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)   # per locale
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    address: Mapped[str | None] = mapped_column(String(300))
    service_area_km: Mapped[float | None] = mapped_column(Float)
    hours: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)        # see app/places/hours.py
    active: Mapped[bool] = mapped_column(default=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Event(_Provenance, Base):
    """Something happening at a time: concert, market, festival, match..."""

    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("region", "slug"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    region: Mapped[str] = mapped_column(String(64), index=True)
    slug: Mapped[str] = mapped_column(String(96))
    title: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)        # per locale
    category: Mapped[str] = mapped_column(String(64))
    tags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    place_id: Mapped[str | None] = mapped_column(ForeignKey("places.id"))
    start_at: Mapped[datetime] = mapped_column(index=True)
    end_at: Mapped[datetime | None] = mapped_column()
    ticket_required: Mapped[bool] = mapped_column(default=False)
    ticket_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    age_limit: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(16))
    booking_source: Mapped[str | None] = mapped_column(String(200))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Offering(_Provenance, Base):
    """Something that can be reserved, rented, bought or booked: a table at
    a restaurant, a ski set, a guided day, a rafting trip. Sold through the
    transaction layer by `provider`; `place` is where it happens (optional)."""

    __tablename__ = "offerings"
    __table_args__ = (UniqueConstraint("region", "slug"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    region: Mapped[str] = mapped_column(String(64), index=True)
    slug: Mapped[str] = mapped_column(String(96))
    service_type: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    place_id: Mapped[str | None] = mapped_column(ForeignKey("places.id"))
    # An event's ticket: the event exists on its own (discovery); this optional
    # link is how it becomes transactable.
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"))
    provider_id: Mapped[str | None] = mapped_column(ForeignKey("external_providers.id"))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    price_from: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    active: Mapped[bool] = mapped_column(default=True)
    pricing: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)    # see app/marketplace/pricing.py
    policies: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)   # overrides the provider's
    commission_type: Mapped[str | None] = mapped_column(String(32))
    commission_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    partner_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # what the provider charges us
    guest_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))    # list price to guests, if fixed


class AvailabilitySlot(Base):
    """Bookable capacity of an offering. Deterministic demo inventory today,
    provider-synchronised later. The bot never claims availability that is
    not recorded here or returned by a provider."""

    __tablename__ = "availability"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    offering_id: Mapped[str] = mapped_column(ForeignKey("offerings.id", ondelete="CASCADE"), index=True)
    starts_at: Mapped[datetime] = mapped_column()
    ends_at: Mapped[datetime] = mapped_column()
    capacity: Mapped[int] = mapped_column(Integer)
    remaining: Mapped[int] = mapped_column(Integer)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)   # e.g. {"languages": ["ru"]}
    source: Mapped[str] = mapped_column(String(255), default="unknown")
    last_verified_at: Mapped[datetime | None] = mapped_column()


class HoldStatus(str, enum.Enum):
    HELD = "held"             # reserved for an open quote until expires_at
    CONFIRMED = "confirmed"   # guest consented; kept while the booking is alive
    RELEASED = "released"     # quote expired/declined/superseded, booking rejected/cancelled/failed


class InventoryHold(Base):
    """Capacity taken from an offering for a time window (and variant, e.g.
    ski length 170). Availability = slot capacity - live holds, so an
    expired quote frees inventory without any sweeper, and two guests can
    never both get the last item (holds are created under a row lock)."""

    __tablename__ = "inventory_holds"
    __table_args__ = (Index("ix_inventory_holds_window", "offering_id", "starts_at", "ends_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    offering_id: Mapped[str] = mapped_column(ForeignKey("offerings.id", ondelete="CASCADE"))
    variant: Mapped[str | None] = mapped_column(String(64))
    starts_at: Mapped[datetime] = mapped_column()
    ends_at: Mapped[datetime] = mapped_column()
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[HoldStatus] = mapped_column(_enum(HoldStatus), default=HoldStatus.HELD)
    expires_at: Mapped[datetime | None] = mapped_column()
    quote_id: Mapped[str | None] = mapped_column(ForeignKey("quotes.id"), index=True)
    transaction_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ItineraryItem(Base):
    """One entry of a stay's trip plan. Saved/shortlisted items carry their
    own lightweight status; items linked to a quote or action never store a
    status of their own - it is read from the quote/action."""

    __tablename__ = "itinerary_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    stay_id: Mapped[str] = mapped_column(ForeignKey("stays.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))             # taxonomy category, e.g. FOOD, TRANSPORT
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[ItemStatus] = mapped_column(_enum(ItemStatus), default=ItemStatus.SAVED)
    starts_at: Mapped[datetime | None] = mapped_column()
    place_id: Mapped[str | None] = mapped_column(ForeignKey("places.id"))
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"))
    offering_id: Mapped[str | None] = mapped_column(ForeignKey("offerings.id"))
    quote_id: Mapped[str | None] = mapped_column(ForeignKey("quotes.id"))
    action_id: Mapped[str | None] = mapped_column(ForeignKey("actions.id"))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


# ---------------------------------------------------- Core v1 aliases
Hotel = Property
HotelKnowledgeDocument = KnowledgeDocument
HotelRequest = Action
