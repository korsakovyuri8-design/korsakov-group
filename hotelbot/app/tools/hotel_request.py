"""`create_hotel_request` - Core v1 tool, kept for compatibility.

Maps the v1 request type to the generic action type and delegates to
`submit_action`, so capability checks and status authority still apply.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.actions.catalog import action_for_topic
from app.db.models import RequestType, Urgency
from app.tools.actions import SubmitActionArgs, SubmitActionTool
from app.tools.registry import ToolContext, ToolResult


class CreateHotelRequestArgs(BaseModel):
    request_type: RequestType
    summary: str = Field(min_length=1, max_length=1000)
    urgency: Urgency = Urgency.NORMAL
    guest_message: str | None = Field(default=None, max_length=4000)
    details: dict[str, Any] = Field(default_factory=dict)


class CreateHotelRequestTool:
    name = "create_hotel_request"
    description = "Deprecated alias of submit_action using Core v1 request types."
    args_model = CreateHotelRequestArgs

    def run(self, ctx: ToolContext, args: CreateHotelRequestArgs) -> ToolResult:
        result = SubmitActionTool().run(ctx, SubmitActionArgs(
            action_type=action_for_topic(args.request_type), summary=args.summary, urgency=args.urgency,
            guest_message=args.guest_message, details=args.details,
        ))
        if result.ok:
            result.data["request_id"] = result.data["action_id"]
        return result
