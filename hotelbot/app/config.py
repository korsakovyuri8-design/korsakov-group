"""Application settings, loaded from environment variables (see .env.example).

Secrets live only here and are never logged: `Settings.__repr__` is
overridden to hide them.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="HOTELBOT_", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    # --- Database -------------------------------------------------------
    # PostgreSQL in Docker Compose; SQLite is fine for local hacking and tests.
    database_url: str = "sqlite:///./hotelbot.db"

    # --- Property packs ----------------------------------------------------
    # Default property for channels that do not identify one. HOTELBOT_HOTEL_SLUG
    # is the Core v1 name and still works; HOTELBOT_PROPERTY_SLUG wins if set.
    hotel_slug: str = "example-hotel"
    property_slug: str | None = None
    knowledge_path: str = "data/hotel/example_hotel.yaml"
    # Additional properties served by this deployment (JSON list of pack paths).
    extra_pack_paths: list[str] = Field(default_factory=list)
    # Local travel data: places, events, offerings, regional providers.
    region_pack_paths: list[str] = Field(default_factory=lambda: ["data/regions/zabljak_demo.yaml"])
    # Minimum retrieval score for an answer to count as grounded.
    grounding_min_score: float = 0.5

    # --- LLM provider ----------------------------------------------------
    # "none" = no LLM; the bot answers extractively from the knowledge pack.
    llm_provider: Literal["none", "openai", "anthropic"] = "none"
    llm_model: str = ""
    llm_timeout_seconds: float = 20.0
    llm_max_tokens: int = 600
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: SecretStr | None = None
    anthropic_base_url: str = "https://api.anthropic.com"

    # --- WhatsApp --------------------------------------------------------
    whatsapp_transport: Literal["mock", "meta"] = "mock"
    whatsapp_verify_token: SecretStr | None = None
    whatsapp_app_secret: SecretStr | None = None
    whatsapp_access_token: SecretStr | None = None
    whatsapp_phone_number_id: str = ""
    whatsapp_graph_version: str = "v21.0"
    whatsapp_timeout_seconds: float = 10.0

    # --- Staff API -------------------------------------------------------
    staff_api_token: SecretStr | None = None

    # --- Agent policy ----------------------------------------------------
    # Consecutive turns the bot could not help with before escalating
    # (default; packs and the overrides below may refine it per intent).
    max_consecutive_failures: int = Field(default=2, ge=1)
    # Per-intent thresholds, e.g. HOTELBOT_FAILURE_THRESHOLDS='{"LOCAL_RECOMMENDATION": 1}'.
    failure_thresholds: dict[str, int] = Field(default_factory=dict)
    # Messages included in a handoff package.
    handoff_context_messages: int = 10
    enable_demo_endpoints: bool = True

    max_inbound_chars: int = Field(default=2000, ge=1)

    # Apply Alembic migrations on startup for existing databases (fresh
    # databases are created from the models and stamped at head).
    auto_migrate: bool = True

    # Background job worker inside the API process (outbox: provider calls,
    # notifications, inbound WhatsApp). Disable to run `python -m app.worker`
    # as separate processes instead (several workers are safe on PostgreSQL).
    worker_enabled: bool = True
    worker_poll_seconds: float = Field(default=1.0, gt=0)

    @property
    def default_property_slug(self) -> str:
        return self.property_slug or self.hotel_slug

    @property
    def all_pack_paths(self) -> list[str]:
        return [self.knowledge_path, *self.extra_pack_paths]


@lru_cache
def get_settings() -> Settings:
    return Settings()
