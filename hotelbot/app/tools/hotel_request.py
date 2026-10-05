"""`create_hotel_request` - put an actionable request in the staff queue."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.agent.memory import known_facts
from app.db.models import RequestType, Urgency
from app.db.repositories import RequestRepository
from app.tools.registry import ToolContext, ToolResult


class CreateHotelRequestArgs(BaseModel):
    request_type: RequestType
    summary: str = Field(min_length=1, max_length=1000)
    urgency: Urgency = Urgency.NORMAL
    guest_message: str | None = Field(default=None, max_length=4000)
    details: dict[str, Any] = Field(default_factory=dict)


class CreateHotelRequestTool:
    name = "create_hotel_request"
    description = (
        "Create a request for hotel staff (late check-in, housekeeping, maintenance, restaurant, "
        "transport, booking, other). Use whenever the guest asks the hotel to do or approve something. "
        "The request is NOT confirmed until staff act on it."
    )
    args_model = CreateHotelRequestArgs

    def run(self, ctx: ToolContext, args: CreateHotelRequestArgs) -> ToolResult:
        details = {
            **args.details,
            "guest_message": args.guest_message,
            # Snapshot of what the guest told us, explicitly unverified.
            "guest_stated_facts": known_facts(ctx.conversation.memory or {}),
            "language": ctx.conversation.language,
        }
        req = RequestRepository(ctx.session).create(
            ctx.conversation, args.request_type, args.summary, args.urgency, details
        )
        return ToolResult(ok=True, data={"request_id": req.id, "request_type": req.request_type.value})
