"""Composition root: builds every component from Settings.

Tests construct a Container with overrides (in-memory DB, scripted LLM,
mock transport) instead of patching globals.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.agent.intents import HybridIntentClassifier
from app.agent.orchestrator import Orchestrator
from app.agent.responder import GroundedResponder
from app.config import Settings
from app.db.repositories import HotelRepository
from app.db.session import create_schema, make_engine, make_session_factory
from app.knowledge.ingest import ingest_pack, load_pack
from app.knowledge.service import KnowledgeService
from app.llm.base import LLMProvider
from app.llm.factory import build_llm
from app.tools import default_registry
from app.whatsapp.base import MessageTransport
from app.whatsapp.meta import MetaWhatsAppTransport
from app.whatsapp.mock import MockWhatsAppTransport

_UNSET = object()


@dataclass
class Container:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    llm: LLMProvider | None
    knowledge: KnowledgeService
    orchestrator: Orchestrator
    whatsapp: MessageTransport
    # Outbound sink for non-WhatsApp channels (demo API) and the mock transport.
    dev_outbox: MockWhatsAppTransport

    def transport_for(self, channel: str) -> MessageTransport:
        return self.whatsapp if channel == "whatsapp" else self.dev_outbox

    def reload_knowledge(self) -> None:
        with self.session_factory() as session:
            report = ingest_pack(session, load_pack(self.settings.knowledge_path))
            session.commit()
            self.knowledge.retriever = KnowledgeService.from_db(
                session, report.hotel_id, self.settings.grounding_min_score
            ).retriever


def build_container(
    settings: Settings,
    *,
    llm: LLMProvider | None | object = _UNSET,
    whatsapp: MessageTransport | None = None,
) -> Container:
    engine = make_engine(settings.database_url)
    create_schema(engine)
    session_factory = make_session_factory(engine)

    pack = load_pack(settings.knowledge_path)
    if pack.pack.hotel_slug != settings.hotel_slug:
        raise ValueError(
            f"knowledge pack is for {pack.pack.hotel_slug!r} but HOTELBOT_HOTEL_SLUG={settings.hotel_slug!r}"
        )
    with session_factory() as session:
        ingest_pack(session, pack)
        session.commit()
        hotel = HotelRepository(session).get_by_slug(settings.hotel_slug)
        assert hotel is not None
        knowledge = KnowledgeService.from_db(session, hotel.id, settings.grounding_min_score)
        hotel_name = hotel.name

    llm_provider = build_llm(settings) if llm is _UNSET else llm  # type: ignore[assignment]
    orchestrator = Orchestrator(
        session_factory=session_factory,
        hotel_slug=settings.hotel_slug,
        knowledge=knowledge,
        classifier=HybridIntentClassifier(llm_provider),  # type: ignore[arg-type]
        responder=GroundedResponder(llm_provider, hotel_name),  # type: ignore[arg-type]
        tools=default_registry(),
        max_consecutive_failures=settings.max_consecutive_failures,
        handoff_context_messages=settings.handoff_context_messages,
        max_inbound_chars=settings.max_inbound_chars,
    )
    dev_outbox = MockWhatsAppTransport()
    if whatsapp is None:
        if settings.whatsapp_transport == "meta":
            whatsapp = MetaWhatsAppTransport(
                access_token=settings.whatsapp_access_token.get_secret_value() if settings.whatsapp_access_token else "",
                phone_number_id=settings.whatsapp_phone_number_id,
                graph_version=settings.whatsapp_graph_version,
                timeout=settings.whatsapp_timeout_seconds,
            )
        else:
            whatsapp = dev_outbox
    return Container(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        llm=llm_provider,  # type: ignore[arg-type]
        knowledge=knowledge,
        orchestrator=orchestrator,
        whatsapp=whatsapp,
        dev_outbox=dev_outbox,
    )
