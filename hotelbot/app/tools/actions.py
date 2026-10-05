"""`submit_action` - the generic action tool.

Checks the property's capability registry first: an action type the
property does not offer is *not* faked - the tool reports it unavailable
and the agent explains the limitation. Otherwise the action is proposed and
handed to the executor the capability declares (staff queue, webhook...).
The resulting status comes from the executor, never from the caller.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.actions.catalog import ACTION_CATALOG
from app.agent.memory import known_facts
from app.db.models import Urgency
from app.tools.registry import ToolContext, ToolResult


class SubmitActionArgs(BaseModel):
    action_type: str = Field(min_length=1, max_length=64)
    summary: str = Field(min_length=1, max_length=1000)
    urgency: Urgency | None = None
    guest_message: str | None = Field(default=None, max_length=4000)
    details: dict[str, Any] = Field(default_factory=dict)


class SubmitActionTool:
    name = "submit_action"
    description = (
        "Submit a real-world action for the guest (late arrival, housekeeping, repair, transport, ...). "
        f"action_type is one of: {', '.join(sorted(ACTION_CATALOG))}. Only works if the property offers "
        "it. The returned status is authoritative; SUBMITTED does not mean accepted."
    )
    args_model = SubmitActionArgs

    def run(self, ctx: ToolContext, args: SubmitActionArgs) -> ToolResult:
        if ctx.capabilities is None or ctx.actions is None:
            return ToolResult(ok=False, error="action layer not configured")
        capability = ctx.capabilities.action(args.action_type)
        if capability is None:
            return ToolResult(ok=False, error="capability_unavailable",
                              data={"action_type": args.action_type, "available": False})
        stay = ctx.stay
        params = {
            **args.details,
            "guest_message": args.guest_message,
            # Snapshot of what the guest told us, explicitly unverified.
            "guest_stated_facts": known_facts(stay.facts or {}) if stay else {},
            "language": ctx.conversation.language,
        }
        action = ctx.actions.propose(
            property_id=ctx.conversation.property_id,
            stay_id=stay.id if stay else None,
            conversation_id=ctx.conversation.id,
            action_type=args.action_type,
            summary=args.summary,
            executor=capability.executor,
            params=params,
            urgency=args.urgency,
        )
        ctx.actions.submit(action, capability.config)
        return ToolResult(ok=True, data={"action_id": action.id, "action_type": action.action_type,
                                         "status": action.status.value, "executor": action.executor})
