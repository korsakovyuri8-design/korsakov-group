"""Per-property runtime: everything the agent needs to serve one property.

The agent core is property-agnostic; it receives a PropertyRuntime for the
property a message is addressed to (knowledge, capabilities, policy).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent.policies import FailurePolicy
from app.capabilities.registry import CapabilityRegistry
from app.knowledge.service import KnowledgeService


@dataclass
class PropertyRuntime:
    property_id: str
    slug: str
    name: str
    property_type: str
    is_synthetic: bool
    knowledge: KnowledgeService
    capabilities: CapabilityRegistry
    failure_policy: FailurePolicy
    emergency_number: str | None = None
    whatsapp_phone_number_id: str | None = None


class UnknownProperty(LookupError):
    pass


class PropertyDirectory:
    def __init__(self, runtimes: list[PropertyRuntime], default_slug: str) -> None:
        self._by_slug = {r.slug: r for r in runtimes}
        if default_slug not in self._by_slug:
            raise UnknownProperty(f"default property {default_slug!r} is not loaded")
        self.default_slug = default_slug

    def get(self, slug: str | None = None) -> PropertyRuntime:
        try:
            return self._by_slug[slug or self.default_slug]
        except KeyError:
            raise UnknownProperty(f"unknown property {slug!r}") from None

    def for_whatsapp_number(self, phone_number_id: str | None) -> PropertyRuntime:
        for runtime in self._by_slug.values():
            if phone_number_id and runtime.whatsapp_phone_number_id == phone_number_id:
                return runtime
        return self.get()

    def replace(self, runtime: PropertyRuntime) -> None:
        self._by_slug[runtime.slug] = runtime

    def all(self) -> list[PropertyRuntime]:
        return list(self._by_slug.values())
