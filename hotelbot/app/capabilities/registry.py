"""Per-property capability registry.

The orchestrator never assumes that every property can do everything. It
asks the registry:

    registry.action("transport_booking")  -> ActionCapability | None
    registry.has_integration("human_staff")
    registry.knowledge_topics              -> topics the knowledge pack covers

Capabilities are declared per property in its pack (YAML `capabilities:`)
and stored on Property.capabilities. Connecting a property to a new
integration (e.g. a transport partner webhook) is a data change - the agent
core does not change.

A property without a declared capability set gets the Core v1 behaviour:
every catalogue action, handled by staff.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.actions.catalog import ACTION_CATALOG

KNOWN_EXECUTORS = ("staff", "webhook")
KNOWN_INTEGRATIONS = ("human_staff", "pms", "payment")


class ActionCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executor: str = "staff"
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> ActionCapability:
        if self.executor not in KNOWN_EXECUTORS:
            raise ValueError(f"unknown executor {self.executor!r}; expected one of {KNOWN_EXECUTORS}")
        if self.executor == "webhook" and not self.config.get("url"):
            raise ValueError("webhook executor requires config.url")
        return self


class ServiceCapability(BaseModel):
    """An external, transactional service (quote -> consent -> provider)."""

    model_config = ConfigDict(extra="forbid")

    # Preferred provider slug; None = discover any active provider offering it.
    provider: str | None = None


class CapabilitySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: dict[str, ActionCapability] = Field(default_factory=dict)
    services: dict[str, ServiceCapability] = Field(default_factory=dict)
    integrations: list[str] = Field(default_factory=list)
    # Informational: external service categories available around the property
    # (e.g. "transport", "activities"), surfaced to staff and future planners.
    external_services: list[str] = Field(default_factory=list)

    @field_validator("actions")
    @classmethod
    def _known_actions(cls, v: dict[str, ActionCapability]) -> dict[str, ActionCapability]:
        unknown = sorted(set(v) - set(ACTION_CATALOG))
        if unknown:
            raise ValueError(f"unknown action types {unknown}; known: {sorted(ACTION_CATALOG)}")
        return v

    @field_validator("services")
    @classmethod
    def _known_services(cls, v: dict[str, ServiceCapability]) -> dict[str, ServiceCapability]:
        from app.transactions.catalog import SERVICE_CATALOG

        unknown = sorted(set(v) - set(SERVICE_CATALOG))
        if unknown:
            raise ValueError(f"unknown service types {unknown}; known: {sorted(SERVICE_CATALOG)}")
        return v

    @field_validator("integrations")
    @classmethod
    def _known_integrations(cls, v: list[str]) -> list[str]:
        unknown = sorted(set(v) - set(KNOWN_INTEGRATIONS))
        if unknown:
            raise ValueError(f"unknown integrations {unknown}; known: {KNOWN_INTEGRATIONS}")
        return v

    @model_validator(mode="after")
    def _staff_needs_humans(self) -> CapabilitySpec:
        staff_actions = [k for k, a in self.actions.items() if a.executor == "staff"]
        if staff_actions and "human_staff" not in self.integrations:
            raise ValueError(f"actions {staff_actions} use the staff executor but 'human_staff' is not an integration")
        return self


def legacy_capabilities() -> CapabilitySpec:
    """Core v1 behaviour: every action type goes to the staff queue."""
    return CapabilitySpec(actions={k: ActionCapability() for k in ACTION_CATALOG}, integrations=["human_staff"])


class CapabilityRegistry:
    def __init__(self, spec: CapabilitySpec, knowledge_topics: frozenset[str] = frozenset()) -> None:
        self.spec = spec
        self.knowledge_topics = knowledge_topics

    @classmethod
    def from_stored(cls, stored: dict[str, Any] | None, knowledge_topics: frozenset[str] = frozenset()) -> CapabilityRegistry:
        spec = CapabilitySpec.model_validate(stored) if stored else legacy_capabilities()
        return cls(spec, knowledge_topics)

    def action(self, action_type: str) -> ActionCapability | None:
        return self.spec.actions.get(action_type)

    def can(self, action_type: str) -> bool:
        return action_type in self.spec.actions

    def service(self, service_type: str) -> ServiceCapability | None:
        return self.spec.services.get(service_type)

    def has_integration(self, name: str) -> bool:
        return name in self.spec.integrations

    def describe(self) -> dict[str, Any]:
        return {
            "knowledge": sorted(self.knowledge_topics),
            "actions": {k: a.executor for k, a in sorted(self.spec.actions.items())},
            "unavailable_actions": sorted(set(ACTION_CATALOG) - set(self.spec.actions)),
            "services": {k: (v.provider or "auto") for k, v in sorted(self.spec.services.items())},
            "integrations": sorted(self.spec.integrations),
            "external_services": sorted(self.spec.external_services),
        }
