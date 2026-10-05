"""Staff/demo API response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import (
    ActionStatus,
    ConversationStatus,
    HandoffStatus,
    MessageRole,
    RequestStatus,
    StayStatus,
)


class _ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MessageOut(_ORM):
    id: str
    role: MessageRole
    text: str
    language: str | None
    intent: str | None
    created_at: datetime


class ConversationSummaryOut(_ORM):
    id: str
    guest_external_id: str
    property_id: str | None = None
    stay_id: str | None = None
    channel: str
    language: str | None
    status: ConversationStatus
    created_at: datetime
    updated_at: datetime


class ConversationOut(ConversationSummaryOut):
    memory: dict[str, Any]
    messages: list[MessageOut]


class HotelRequestOut(_ORM):
    id: str
    conversation_id: str
    request_type: str
    status: str
    urgency: str
    summary: str
    details: dict[str, Any]
    resolution_note: str | None
    created_at: datetime
    resolved_at: datetime | None


class HandoffOut(_ORM):
    id: str
    conversation_id: str
    reason: str
    urgency: str
    status: HandoffStatus
    package: dict[str, Any]
    created_at: datetime
    resolved_at: datetime | None


class ResolveRequestIn(BaseModel):
    status: RequestStatus = RequestStatus.RESOLVED
    note: str | None = Field(default=None, max_length=2000)
    # Optional message to the guest, sent through their channel.
    reply_to_guest: str | None = Field(default=None, max_length=4000)


class StaffMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    staff_name: str | None = Field(default=None, max_length=100)


class ResolveHandoffIn(BaseModel):
    # Hand the conversation back to the bot (default) or close it.
    close_conversation: bool = False


# ------------------------------------------------------------- Stay Engine
class ActionEventOut(_ORM):
    seq: int
    from_status: ActionStatus | None
    to_status: ActionStatus
    actor: str
    detail: str | None
    created_at: datetime


class ActionOut(_ORM):
    id: str
    property_id: str
    stay_id: str | None
    conversation_id: str | None
    action_type: str
    status: ActionStatus
    urgency: str
    summary: str
    params: dict[str, Any]
    result: dict[str, Any]
    executor: str
    external_ref: str | None
    error: str | None
    note: str | None
    created_at: datetime
    closed_at: datetime | None


class ActionDetailOut(ActionOut):
    events: list[ActionEventOut]


class ActionTransitionIn(BaseModel):
    to: ActionStatus
    note: str | None = Field(default=None, max_length=2000)
    staff_name: str | None = Field(default=None, max_length=100)
    # Send the guest the status template for the new state.
    notify_guest: bool = True


class ActionTransitionOut(BaseModel):
    action: ActionDetailOut
    guest_notification: str | None


class StayOut(_ORM):
    id: str
    guest_id: str
    property_id: str
    status: StayStatus
    booking_reference: str | None
    arrival_at: datetime | None
    departure_at: datetime | None
    party_size: int | None
    source_channel: str | None
    facts: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class StayUpdateIn(BaseModel):
    """Staff-verified (authoritative) stay data."""

    status: StayStatus | None = None
    booking_reference: str | None = Field(default=None, max_length=128)
    arrival_at: datetime | None = None
    departure_at: datetime | None = None
    party_size: int | None = Field(default=None, ge=1, le=100)


class PropertyOut(BaseModel):
    id: str
    slug: str
    name: str
    property_type: str
    timezone: str
    default_language: str
    active: bool
    is_synthetic: bool
    capabilities: dict[str, Any]
