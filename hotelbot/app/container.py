"""Composition root: builds every component from Settings.

Tests and the evaluation harness construct a Container with overrides
(in-memory DB, scripted LLM, mock transport, mock HTTP for integrations)
instead of patching globals.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.actions.executors import ExecutorRegistry, default_executors
from app.agent.intents import HybridIntentClassifier
from app.agent.orchestrator import Orchestrator
from app.agent.policies import FailurePolicy
from app.agent.responder import GroundedResponder
from app.agent.runtime import PropertyDirectory, PropertyRuntime
from app.capabilities.registry import CapabilityRegistry
from app.config import Settings
from app.db.models import Property
from app.db.repositories import PropertyRepository
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
    properties: PropertyDirectory
    executors: ExecutorRegistry
    orchestrator: Orchestrator
    whatsapp: MessageTransport
    # Outbound sink for non-WhatsApp channels (demo API) and the mock transport.
    dev_outbox: MockWhatsAppTransport

    @property
    def knowledge(self) -> KnowledgeService:
        """Default property's knowledge (Core v1 attribute)."""
        return self.properties.get().knowledge

    def transport_for(self, channel: str) -> MessageTransport:
        return self.whatsapp if channel == "whatsapp" else self.dev_outbox

    def runtime_for_property_id(self, property_id: str) -> PropertyRuntime | None:
        return next((r for r in self.properties.all() if r.property_id == property_id), None)

    def reload_knowledge(self) -> None:
        """Re-ingest every pack and swap the runtimes in place."""
        with self.session_factory() as session:
            for path in self.settings.all_pack_paths:
                report = ingest_pack(session, load_pack(path))
                prop = PropertyRepository(session).get(report.property_id)
                assert prop is not None
                self.properties.replace(build_runtime(session, prop, self.settings))
            session.commit()


def build_runtime(session: Session, prop: Property, settings: Settings) -> PropertyRuntime:
    knowledge = KnowledgeService.from_db(session, prop.id, settings.grounding_min_score)
    extra = prop.extra or {}
    return PropertyRuntime(
        property_id=prop.id,
        slug=prop.slug,
        name=prop.name,
        property_type=prop.property_type.value,
        is_synthetic=prop.is_synthetic,
        knowledge=knowledge,
        capabilities=CapabilityRegistry.from_stored(prop.capabilities, knowledge.topics),
        failure_policy=FailurePolicy.resolve(settings.max_consecutive_failures, settings.failure_thresholds,
                                             extra.get("policy")),
        emergency_number=extra.get("emergency_number"),
        whatsapp_phone_number_id=extra.get("whatsapp_phone_number_id"),
    )


def build_container(
    settings: Settings,
    *,
    llm: LLMProvider | None | object = _UNSET,
    whatsapp: MessageTransport | None = None,
    http_client: httpx.Client | None = None,
) -> Container:
    engine = make_engine(settings.database_url)
    create_schema(engine, auto_migrate=settings.auto_migrate)
    session_factory = make_session_factory(engine)

    packs = [load_pack(path) for path in settings.all_pack_paths]
    slugs = [p.pack.property_slug for p in packs]
    if settings.default_property_slug not in slugs:
        raise ValueError(
            f"no loaded pack is for the default property {settings.default_property_slug!r} (packs: {slugs})"
        )
    runtimes = []
    with session_factory() as session:
        for pack in packs:
            report = ingest_pack(session, pack)
            prop = PropertyRepository(session).get(report.property_id)
            assert prop is not None
            runtimes.append(build_runtime(session, prop, settings))
        session.commit()
    properties = PropertyDirectory(runtimes, settings.default_property_slug)
    default = properties.get()

    llm_provider = build_llm(settings) if llm is _UNSET else llm  # type: ignore[assignment]
    executors = default_executors(http_client)
    orchestrator = Orchestrator(
        session_factory=session_factory,
        properties=properties,
        classifier=HybridIntentClassifier(llm_provider),  # type: ignore[arg-type]
        responder=GroundedResponder(llm_provider, default.name, default.property_type),  # type: ignore[arg-type]
        tools=default_registry(),
        executors=executors,
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
        properties=properties,
        executors=executors,
        orchestrator=orchestrator,
        whatsapp=whatsapp,
        dev_outbox=dev_outbox,
    )
