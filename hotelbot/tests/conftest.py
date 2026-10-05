from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import MetaData

from app.config import Settings
from app.container import Container, build_container
from app.db.session import make_engine
from app.llm.base import ChatMessage, LLMError
from app.main import create_app
from app.schemas.messages import AgentReply, InboundMessage
from app.whatsapp.mock import MockWhatsAppTransport

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PACK = str(ROOT / "data" / "hotel" / "example_hotel.yaml")
STAFF_TOKEN = "test-staff-token"
VERIFY_TOKEN = "test-verify-token"
APP_SECRET = "test-app-secret"
# Set to e.g. postgresql+psycopg://hotelbot:hotelbot@localhost:5432/hotelbot_test
# to run the whole suite against PostgreSQL instead of in-memory SQLite.
TEST_DATABASE_URL = os.environ.get("HOTELBOT_TEST_DATABASE_URL", "sqlite://")


def _fresh_database_url() -> str:
    if not TEST_DATABASE_URL.startswith("sqlite"):
        engine = make_engine(TEST_DATABASE_URL)
        # Reflect rather than use Base.metadata: tables from older schema
        # versions (and alembic_version) must go too.
        existing = MetaData()
        existing.reflect(engine)
        existing.drop_all(engine)
        engine.dispose()
    return TEST_DATABASE_URL


class ScriptedLLM:
    """Test double implementing the LLMProvider protocol: returns queued
    responses (str) or raises queued exceptions, and records every call."""

    name = "scripted"

    def __init__(self, responses: list[str | Exception] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    def queue(self, *items: str | Exception | dict) -> None:
        self.responses.extend(json.dumps(i) if isinstance(i, dict) else i for i in items)

    def complete(self, system: str, messages: list[ChatMessage], *, max_tokens: int | None = None,
                 temperature: float = 0.2) -> str:
        self.calls.append({"system": system, "messages": messages})
        if not self.responses:
            raise LLMError("no scripted response")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_settings(**overrides) -> Settings:
    base = dict(
        env="test",
        database_url=_fresh_database_url(),
        knowledge_path=EXAMPLE_PACK,
        hotel_slug="example-hotel",
        staff_api_token=STAFF_TOKEN,
        whatsapp_verify_token=VERIFY_TOKEN,
        whatsapp_app_secret=APP_SECRET,
        log_level="WARNING",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def transport() -> MockWhatsAppTransport:
    return MockWhatsAppTransport()


@pytest.fixture
def container(settings: Settings, transport: MockWhatsAppTransport) -> Container:
    return build_container(settings, llm=None, whatsapp=transport)


@pytest.fixture
def client(container: Container) -> Iterator[TestClient]:
    with TestClient(create_app(container=container)) as c:
        yield c


@pytest.fixture
def staff_headers() -> dict[str, str]:
    return {"X-Staff-Token": STAFF_TOKEN}


@pytest.fixture
def chat(container: Container):
    """Send a message straight to the orchestrator (no HTTP)."""

    def _chat(text: str, guest: str = "guest-1", channel: str = "demo", external_id: str | None = None) -> AgentReply:
        return container.orchestrator.handle(
            InboundMessage(channel=channel, sender_id=guest, text=text, external_id=external_id)
        )

    return _chat
