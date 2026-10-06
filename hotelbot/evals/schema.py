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

CATEGORIES = ("grounding", "actions", "authority", "handoff", "safety", "memory", "conversation", "languages",
              "transactions", "local", "marketplace")


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
    # Transactions
    quote: dict[str, Any] | None = None          # latest quote: {status, amount, currency}
    no_new_quote: bool | None = None
    transaction: dict[str, Any] | None = None    # latest transaction: {status, ...request fields}
    notifications_contain: list[str] = Field(default_factory=list)    # async messages to the guest
    notifications_forbidden: list[str] = Field(default_factory=list)
    no_notifications: bool | None = None
    callback_status: int | None = None
    callback_result: str | None = None


class StaffTransition(_Strict):
    to: str
    action: Literal["last"] = "last"
    note: str | None = None


class StayUpdate(_Strict):
    status: str | None = None
    party_size: int | None = None


class ProviderCallback(_Strict):
    event: str
    event_id: str = "evt-1"
    signature: Literal["valid", "invalid", "missing"] = "valid"
    timestamp_offset_seconds: int = 0
    reference: str = "auto"                    # "auto" = latest transaction's provider reference
    provider: str = "demo-transfers"


class Step(_Strict):
    guest: str | None = None
    guest_id: str = "eval-guest"
    # Channel message id (e.g. a WhatsApp wamid): the same id twice = a redelivery.
    external_id: str | None = None
    property: str | None = None                # pack alias or slug; default = first property
    channel: str = "demo"
    staff_transition: StaffTransition | None = None
    stay_update: StayUpdate | None = None
    provider_callback: ProviderCallback | None = None
    advance_minutes: float | None = None
    expect: StepExpect = Field(default_factory=StepExpect)

    @model_validator(mode="after")
    def _one_kind(self) -> Step:
        kinds = [self.guest is not None, self.staff_transition is not None, self.stay_update is not None,
                 self.provider_callback is not None, self.advance_minutes is not None]
        if sum(kinds) != 1:
            raise ValueError("a step is exactly one of: guest, staff_transition, stay_update, provider_callback, "
                             "advance_minutes")
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
    quotes: list[str] | None = None            # statuses of all quotes (multiset)
    transactions: list[str] | None = None      # statuses of all transactions (multiset)
    provider_bookings: int | None = None       # bookings that exist at the (mock) provider
    provider_submit_calls_max: int | None = None
    dead_jobs: int | None = None
    bookings: list[str] | None = None          # "service_type:status" of all provider transactions (multiset)
    plan: list[str] | None = None              # effective statuses of all itinerary items (multiset)


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
    # Merge into provider config of the default property's pack, by slug.
    provider_config: dict[str, dict[str, Any]] | None = None
    # Frozen clock start (ISO, UTC); default is the harness CLOCK_START.
    clock_start: str | None = None
    steps: list[Step]
    final: FinalExpect = Field(default_factory=FinalExpect)
