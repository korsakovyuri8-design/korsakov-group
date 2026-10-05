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
from app.clock import Clock, SystemClock
from app.agent.intents import HybridIntentClassifier
from app.agent.orchestrator import Orchestrator
from app.agent.policies import FailurePolicy
from app.agent.responder import GroundedResponder
from app.agent.runtime import PropertyDirectory, PropertyRuntime
from app.capabilities.registry import CapabilityRegistry
from app.config import Settings
from app.jobs.handlers import register_handlers
from app.jobs.queue import JobWorker
from app.db.models import Property
from app.db.repositories import PropertyRepository
from app.db.session import create_schema, make_engine, make_session_factory
from app.knowledge.ingest import ingest_pack, load_pack
from app.knowledge.service import KnowledgeService
from app.llm.base import LLMProvider
from app.llm.factory import build_llm
from app.tools import default_registry
from app.transactions.dialogue import TransactionDialogue
from app.transactions.providers.registry import ProviderRegistry
from app.transactions.service import TxnDeps
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
    clock: Clock
    providers_registry: ProviderRegistry
    txn_deps: TxnDeps
    worker: JobWorker

    def kick(self, rounds: int = 3) -> None:
        """Run due jobs now (fast path after a request); the background
        worker or `python -m app.worker` remains the safety net."""
        for _ in range(rounds):
            if not self.worker.run_once():
                break

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
        timezone=prop.timezone,
    )


def build_container(
    settings: Settings,
    *,
    llm: LLMProvider | None | object = _UNSET,
    whatsapp: MessageTransport | None = None,
    http_client: httpx.Client | None = None,
    clock: Clock | None = None,
) -> Container:
    clock = clock or SystemClock()
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
    channel_whatsapp = whatsapp

    def transport_for(channel: str) -> MessageTransport:
        return channel_whatsapp if channel == "whatsapp" else dev_outbox

    providers_registry = ProviderRegistry(clock, http_client)
    txn_deps = TxnDeps(clock=clock, providers=providers_registry, executors=executors, transport_for=transport_for,
                       handoff_context=settings.handoff_context_messages)
    orchestrator_ref: list[Orchestrator] = []
    transactions = TransactionDialogue(
        txn_deps,
        submit_staff=lambda turn, action_type, message, summary: orchestrator_ref[0]._submit(
            turn, action_type, guest_message=message, summary=summary),
    )
    orchestrator = Orchestrator(
        session_factory=session_factory,
        properties=properties,
        classifier=HybridIntentClassifier(llm_provider),  # type: ignore[arg-type]
        responder=GroundedResponder(llm_provider, default.name, default.property_type),  # type: ignore[arg-type]
        tools=default_registry(),
        executors=executors,
        handoff_context_messages=settings.handoff_context_messages,
        max_inbound_chars=settings.max_inbound_chars,
        transactions=transactions,
    )
    orchestrator_ref.append(orchestrator)
    container = Container(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        llm=llm_provider,  # type: ignore[arg-type]
        properties=properties,
        executors=executors,
        orchestrator=orchestrator,
        whatsapp=whatsapp,
        dev_outbox=dev_outbox,
        clock=clock,
        providers_registry=providers_registry,
        txn_deps=txn_deps,
        worker=JobWorker(session_factory, clock),
    )
    register_handlers(container.worker, container)
    return container
