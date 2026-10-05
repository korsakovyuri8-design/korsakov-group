"""Property pack format.

A property pack is a YAML (or JSON) file holding everything the bot may
state as fact about one property, plus what the property can *do*
(capabilities) and its conversation policy. It is pure data: swapping the
synthetic pack for the real Hotel Aleksandar pack requires no code change.

Core v1 packs (`hotel_slug` / `hotel_name`, no capabilities) still load:
they get property_type "hotel" and the legacy all-staff capability set.
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.capabilities.registry import CapabilitySpec
from app.db.models import PropertyType
from app.text import latin_to_cyrillic

SUPPORTED_LANGUAGES = ("en", "cnr", "ru")


class KnowledgeItem(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_\-]+$", max_length=128)
    category: str
    # Items sharing a topic describe the same fact; if they disagree, the bot
    # refuses to pick one (see knowledge conflict handling). Defaults to key.
    topic: str | None = None
    # Per-locale text. At least one language must be present.
    content: dict[str, str]
    # Extra retrieval terms per language (synonyms, colloquial words).
    keywords: dict[str, list[str]] = Field(default_factory=dict)
    source: str | None = None  # overrides the pack-level source
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def _non_empty(cls, v: dict[str, str]) -> dict[str, str]:
        if not any(text.strip() for text in v.values()):
            raise ValueError("knowledge item needs content in at least one language")
        return v


class PackInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    property_slug: str = Field(validation_alias=AliasChoices("property_slug", "hotel_slug"))
    property_name: str = Field(validation_alias=AliasChoices("property_name", "hotel_name"))
    property_type: PropertyType = PropertyType.HOTEL
    timezone: str = "UTC"
    default_language: str = "en"
    source: str
    synthetic: bool = False
    version: str | None = None
    # Local emergency number shown in emergency replies (e.g. "112"). Not
    # hard-coded in the core: properties outside the EU differ.
    emergency_number: str | None = None
    # Routes inbound WhatsApp messages for this number to this property.
    whatsapp_phone_number_id: str | None = None
    # Local travel layer: the region pack this property belongs to, and where it is.
    region: str | None = None
    location: dict[str, float] | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {v!r}") from exc
        return v

    # Core v1 names
    @property
    def hotel_slug(self) -> str:
        return self.property_slug

    @property
    def hotel_name(self) -> str:
        return self.property_name


class PolicySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Consecutive unhelpful turns before escalating to staff (None = global setting).
    max_consecutive_failures: int | None = Field(default=None, ge=1)
    # Per-intent overrides, e.g. {"LOCAL_RECOMMENDATION": 1}.
    failure_thresholds: dict[str, int] = Field(default_factory=dict)


_SECRET_HINTS = ("secret", "token", "password", "api_key", "apikey", "credential")


class ProviderSpec(BaseModel):
    """An external provider serving this property. `config` must not hold
    secrets: reference them by environment-variable name (`*_env` keys)."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_\-]*$", max_length=64)
    name: str
    provider_type: str
    integration_type: str = "mock"
    services: list[str]
    active: bool = True
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> ProviderSpec:
        from app.transactions.catalog import PROVIDER_TYPES, SERVICE_CATALOG
        from app.transactions.providers.registry import INTEGRATION_TYPES

        if self.provider_type not in PROVIDER_TYPES:
            raise ValueError(f"unknown provider_type {self.provider_type!r}")
        if self.integration_type not in INTEGRATION_TYPES:
            raise ValueError(f"unknown integration_type {self.integration_type!r}")
        for svc in self.services:
            spec = SERVICE_CATALOG.get(svc)
            if spec is None:
                raise ValueError(f"unknown service type {svc!r}")
            if spec.domain != self.provider_type:
                raise ValueError(f"service {svc!r} belongs to {spec.domain!r}, not {self.provider_type!r}")
        bad = [k for k in self.config if any(h in k.lower() for h in _SECRET_HINTS) and not k.endswith("_env")]
        if bad:
            raise ValueError(f"provider config must not contain secrets {bad}; use '<name>_env' variables")
        if self.integration_type == "webhook" and not self.config.get("base_url"):
            raise ValueError("webhook providers require config.base_url")
        return self


class KnowledgePack(BaseModel):
    pack: PackInfo
    items: list[KnowledgeItem]
    # None = Core v1 pack without declared capabilities (legacy defaults).
    capabilities: CapabilitySpec | None = None
    policy: PolicySpec = Field(default_factory=PolicySpec)
    providers: list[ProviderSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _declared_providers_offer_service(self) -> KnowledgePack:
        """A service pinned to a provider declared in this pack must be offered
        by it. Services may also be fulfilled by region-scoped providers
        (region packs); those are resolved at runtime."""
        if not self.capabilities:
            return self
        own = {p.slug: p for p in self.providers}
        for svc, cap in self.capabilities.services.items():
            if cap.provider in own and svc not in own[cap.provider].services:
                raise ValueError(f"provider {cap.provider!r} does not offer service {svc!r}")
        return self

    @field_validator("items")
    @classmethod
    def _unique_keys(cls, v: list[KnowledgeItem]) -> list[KnowledgeItem]:
        keys = [i.key for i in v]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ValueError(f"duplicate knowledge item keys: {sorted(dupes)}")
        return v


class RetrievedItem(BaseModel):
    """A knowledge item returned by a retriever, with its relevance score."""

    key: str
    category: str
    source: str
    content: dict[str, str]
    metadata: dict[str, Any]
    score: float

    @property
    def topic(self) -> str:
        return self.metadata.get("topic") or self.key

    def has_locale(self, locale: str) -> bool:
        base = locale.split("-")[0]
        return locale in self.content or base in self.content

    def text_for(self, locale: str) -> str:
        """Content in the requested locale (Cyrillic derived from Latin
        Montenegrin when needed), falling back to English, then anything."""
        if locale in self.content:
            return self.content[locale]
        if locale == "cnr-Cyrl" and "cnr" in self.content:
            return latin_to_cyrillic(self.content["cnr"])
        base = locale.split("-")[0]
        return self.content.get(base) or self.content.get("en") or next(iter(self.content.values()))


class RetrievalResult(BaseModel):
    query: str
    items: list[RetrievedItem]
    min_score: float

    @property
    def grounded(self) -> bool:
        return bool(self.items) and self.items[0].score >= self.min_score

    @property
    def grounded_items(self) -> list[RetrievedItem]:
        return [i for i in self.items if i.score >= self.min_score]
