"""Builds provider adapters from ExternalProvider rows (cached per provider)."""

from __future__ import annotations

from typing import Any

import httpx

from app.clock import Clock
from app.db.models import ExternalProvider
from app.transactions.providers.base import ProviderAdapter
from app.transactions.providers.mock import MockExternalProvider
from app.transactions.providers.webhook import WebhookExternalProvider

INTEGRATION_TYPES = ("mock", "webhook")


class ProviderRegistry:
    def __init__(self, clock: Clock, http_client: httpx.Client | None = None) -> None:
        self.clock = clock
        self.http_client = http_client
        self._adapters: dict[str, ProviderAdapter] = {}

    def adapter(self, provider: ExternalProvider) -> ProviderAdapter:
        if provider.id not in self._adapters:
            self._adapters[provider.id] = self._build(provider.slug, provider.integration_type, provider.config or {})
        return self._adapters[provider.id]

    def _build(self, slug: str, integration_type: str, config: dict[str, Any]) -> ProviderAdapter:
        if integration_type == "mock":
            return MockExternalProvider(slug, config, self.clock)
        if integration_type == "webhook":
            return WebhookExternalProvider(slug, config, self.clock, self.http_client)
        raise ValueError(f"unknown integration type {integration_type!r}")

    def override(self, provider_id: str, adapter: ProviderAdapter) -> None:
        """Tests/evaluation: replace the adapter for one provider."""
        self._adapters[provider_id] = adapter

    def instances(self) -> list[ProviderAdapter]:
        return list(self._adapters.values())
