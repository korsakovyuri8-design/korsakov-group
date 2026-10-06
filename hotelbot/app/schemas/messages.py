"""Channel-neutral message contracts between transports/APIs and the agent."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class InboundMessage(BaseModel):
    """A guest message from any channel (WhatsApp, demo API, future web chat)."""

    channel: str = Field(min_length=1, max_length=32)
    sender_id: str = Field(min_length=1, max_length=128)
    text: str
    external_id: str | None = Field(default=None, max_length=255)  # channel message id, for dedupe
    display_name: str | None = Field(default=None, max_length=200)
    # Which property the message is addressed to (None = deployment default).
    property_slug: str | None = Field(default=None, max_length=64)


class ActionTaken(BaseModel):
    # "hotel_request" (Core v1 name for an Action; see detail.action_type/status) | "handoff"
    kind: str
    id: str
    detail: dict[str, Any] = {}


class AgentReply(BaseModel):
    """What the orchestrator decided. `text` is None when the bot must stay
    silent (duplicate delivery, or a human currently owns the conversation)."""

    conversation_id: str | None
    text: str | None
    language: str
    intent: str | None = None
    grounded: bool | None = None
    sources: list[str] = []
    actions: list[ActionTaken] = []
    handed_off: bool = False
    # ANSWER | FIND | RECOMMEND | SAVE | ACTION | TRANSACTION | HANDOFF
    outcomes: list[str] = []
    duplicate: bool = False
    knowledge_synthetic: bool = False
    property_slug: str | None = None
    stay_id: str | None = None
