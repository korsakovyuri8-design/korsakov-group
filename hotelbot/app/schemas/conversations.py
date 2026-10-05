"""Staff/demo API response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import ConversationStatus, HandoffStatus, MessageRole, RequestStatus


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
