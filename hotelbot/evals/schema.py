"""Scenario format for the product-level evaluation harness.

A scenario is a short conversation with expectations about *product
behaviour* (what the guest is told, which actions exist, who owns the
conversation, what is remembered) - not about implementation details.

    - name: late_checkout_authorization
      category: actions
      properties: [hotel]                  # packs to load; first = default
      knowledge: {remove: [...], add: [...]}   # seed-knowledge edits (default property)
      capabilities: {...}                  # replace the default property's capabilities
      integration: {status: 200, json: {...}} | {timeout: true}
      llm: [...]                           # scripted model outputs (otherwise: no LLM)
      steps:
        - guest: "Can I stay until 3pm?"
          expect:
            intent: SERVICE_REQUEST
            actions: [{type: late_checkout_request, status: submitted}]
            forbidden: ["you can stay until", "is confirmed"]
      final:
        memory: {guest_count: {value: 2, confirmed: false}}
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CATEGORIES = ("grounding", "actions", "authority", "handoff", "safety", "memory", "conversation", "languages")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionExpect(_Strict):
    type: str
    status: str | None = None
    executor: str | None = None
    urgency: str | None = None


class StepExpect(_Strict):
    intent: str | list[str] | None = None
    language: str | None = None
    grounded: bool | None = None
    grounded_is_none: bool = False
    sources: list[str] | None = None          # must include these knowledge keys
    reply_contains: list[str] = Field(default_factory=list)       # all, case-insensitive
    reply_contains_any: list[str] = Field(default_factory=list)   # at least one
    forbidden: list[str] = Field(default_factory=list)            # none may appear
    silent: bool | None = None                # True = bot must not reply
    handoff: bool | None = None               # conversation owned by a human after this turn
    handoff_reason: str | None = None
    actions: list[ActionExpect] | None = None # actions created in this turn (exact)
    # Staff-transition steps
    notification_contains: list[str] = Field(default_factory=list)
    notification_forbidden: list[str] = Field(default_factory=list)


class StaffTransition(_Strict):
    to: str
    action: Literal["last"] = "last"
    note: str | None = None


class StayUpdate(_Strict):
    status: str | None = None
    party_size: int | None = None


class Step(_Strict):
    guest: str | None = None
    guest_id: str = "eval-guest"
    property: str | None = None                # pack alias or slug; default = first property
    channel: str = "demo"
    staff_transition: StaffTransition | None = None
    stay_update: StayUpdate | None = None
    expect: StepExpect = Field(default_factory=StepExpect)

    @model_validator(mode="after")
    def _one_kind(self) -> Step:
        kinds = [self.guest is not None, self.staff_transition is not None, self.stay_update is not None]
        if sum(kinds) != 1:
            raise ValueError("a step is exactly one of: guest, staff_transition, stay_update")
        return self


class StayExpect(_Strict):
    property: str | None = None               # alias/slug; default property if omitted
    guest_id: str = "eval-guest"
    facts: dict[str, dict[str, Any]] | None = None    # subset match per fact
    facts_absent: list[str] = Field(default_factory=list)
    facts_empty: bool | None = None
    party_size: int | None = None
    party_size_is_none: bool = False
    status: str | None = None
    count: int | None = None                  # number of stays for this guest at this property


class FinalExpect(_Strict):
    actions: list[ActionExpect] | None = None  # all actions in the scenario (exact multiset)
    handoffs: list[dict[str, str]] | None = None
    stays: list[StayExpect] = Field(default_factory=list)


class Integration(_Strict):
    status: int = 200
    json_body: dict[str, Any] | None = Field(default=None, alias="json")
    timeout: bool = False


class KnowledgeEdit(_Strict):
    remove: list[str] = Field(default_factory=list)
    add: list[dict[str, Any]] = Field(default_factory=list)


class Scenario(_Strict):
    name: str = Field(pattern=r"^[a-z0-9_]+$")
    category: Literal[CATEGORIES]  # type: ignore[valid-type]
    description: str = ""
    # Gate scenarios encode product invariants; pytest fails if they fail.
    gate: bool = False
    properties: list[str] = Field(default_factory=lambda: ["hotel"])
    knowledge: KnowledgeEdit | None = None
    capabilities: dict[str, Any] | None = None
    integration: Integration | None = None
    llm: list[Any] | None = None
    steps: list[Step]
    final: FinalExpect = Field(default_factory=FinalExpect)
