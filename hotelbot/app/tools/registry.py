"""Tool/action registry.

A tool is a named, schema-validated internal action. The orchestrator calls
tools through the registry today; the same registry can later be exposed to
an LLM for function calling without changing the tools themselves.

Tools never execute arbitrary code: they are explicit Python callables with a
Pydantic input model, and the registry rejects unknown names and invalid args.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.db.models import Conversation, Stay
from app.observability import log_event

if TYPE_CHECKING:
    from app.actions.service import ActionService
    from app.capabilities.registry import CapabilityRegistry


@dataclass
class ToolContext:
    session: Session
    conversation: Conversation
    stay: Stay | None = None
    capabilities: CapabilityRegistry | None = None
    actions: ActionService | None = None


class ToolResult(BaseModel):
    ok: bool
    data: dict[str, Any] = {}
    error: str | None = None


class Tool(Protocol):
    name: str
    description: str
    args_model: type[BaseModel]

    def run(self, ctx: ToolContext, args: Any) -> ToolResult: ...


class ToolError(Exception):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ToolError(f"tool {tool.name!r} already registered")
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        """JSON-schema descriptions, ready for LLM function calling."""
        return [
            {"name": t.name, "description": t.description, "input_schema": t.args_model.model_json_schema()}
            for t in self._tools.values()
        ]

    def call(self, name: str, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"unknown tool {name!r}")
        try:
            parsed = tool.args_model.model_validate(args)
        except ValidationError as exc:
            log_event("tool_called", tool=name, conversation_id=ctx.conversation.id, ok=False, error="invalid_args")
            return ToolResult(ok=False, error=f"invalid arguments: {exc.errors()[0]['msg']}")
        result = tool.run(ctx, parsed)
        log_event("tool_called", tool=name, conversation_id=ctx.conversation.id, ok=result.ok, **result.data)
        return result
