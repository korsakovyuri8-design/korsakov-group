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


class ActionTaken(BaseModel):
    kind: str          # "hotel_request" | "handoff"
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
    duplicate: bool = False
    knowledge_synthetic: bool = False
