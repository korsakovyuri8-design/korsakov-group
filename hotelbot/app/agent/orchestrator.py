"""The conversational core. Every channel (WhatsApp webhook, /api/chat demo,
future web chat) calls `Orchestrator.handle()` - there is exactly one path
from guest message to bot decision.

Per message:
 1. idempotency check (channel message id), guest + conversation lookup
 2. language detection, persist guest message, update session memory
 3. intent classification
 4. if a human owns the conversation: stay silent (except emergencies)
 5. resolve a pending yes/no offer, if any
 6. route by intent: handoff / create request / grounded answer / clarify
 7. failure counting -> escalation after repeated failures
 8. persist bot reply; the caller delivers it on its channel
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.agent import messages as msg
from app.agent.handoff import create_handoff
from app.agent.intents import Intent, IntentClassifier, IntentResult
from app.agent.language import detect_language, resolve_reply_language
from app.agent.memory import extract_facts, remember
from app.agent.policies import (
    AFFIRMATIVE,
    NEGATIVE,
    HandoffDecision,
    handoff_for_failures,
    handoff_for_intent,
    request_urgency,
)
from app.agent.responder import GroundedResponder
from app.db.models import Conversation, ConversationStatus, Hotel, MessageRole, RequestType
from app.db.repositories import ConversationRepository, HotelRepository
from app.knowledge.service import KnowledgeService
from app.llm.base import ChatMessage
from app.observability import log_event
from app.schemas.messages import ActionTaken, AgentReply, InboundMessage
from app.text import fold
from app.tools.registry import ToolContext, ToolRegistry

HISTORY_TURNS = 6


@dataclass
class _Turn:
    """Mutable state for handling one inbound message."""

    session: Session
    conv: Conversation
    hotel: Hotel
    inbound: InboundMessage
    text: str
    language: str
    intent: IntentResult | None = None
    reply: str | None = None
    grounded: bool | None = None
    sources: list[str] = field(default_factory=list)
    actions: list[ActionTaken] = field(default_factory=list)
    failed: bool = False  # bot could not help this turn
    succeeded: bool = False

    @property
    def state(self) -> dict[str, Any]:
        return dict((self.conv.memory or {}).get("_state", {}))

    def set_state(self, **values: Any) -> None:
        state = {k: v for k, v in {**self.state, **values}.items() if v is not None}
        self.conv.memory = {**(self.conv.memory or {}), "_state": state}


class Orchestrator:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        hotel_slug: str,
        knowledge: KnowledgeService,
        classifier: IntentClassifier,
        responder: GroundedResponder,
        tools: ToolRegistry,
        *,
        max_consecutive_failures: int = 2,
        handoff_context_messages: int = 10,
        max_inbound_chars: int = 2000,
    ) -> None:
        self.session_factory = session_factory
        self.hotel_slug = hotel_slug
        self.knowledge = knowledge
        self.classifier = classifier
        self.responder = responder
        self.tools = tools
        self.max_failures = max_consecutive_failures
        self.handoff_context = handoff_context_messages
        self.max_chars = max_inbound_chars

    # ------------------------------------------------------------------ entry
    def handle(self, inbound: InboundMessage) -> AgentReply:
        with self.session_factory() as session:
            try:
                reply = self._handle(session, inbound)
                session.commit()
                return reply
            except Exception as exc:
                session.rollback()
                log_event("error", where="orchestrator", error=type(exc).__name__, detail=str(exc)[:200])
                raise

    def _handle(self, session: Session, inbound: InboundMessage) -> AgentReply:
        convs = ConversationRepository(session)
        hotel = HotelRepository(session).get_by_slug(self.hotel_slug)
        if hotel is None:
            raise RuntimeError(f"hotel {self.hotel_slug!r} not loaded; ingest a knowledge pack first")

        if inbound.external_id and convs.message_exists(inbound.external_id):
            log_event("message_duplicate", channel=inbound.channel, external_id=inbound.external_id)
            return AgentReply(conversation_id=None, text=None, language="en", duplicate=True)

        text = inbound.text.strip()[: self.max_chars]
        guest = convs.get_or_create_guest(inbound.channel, inbound.sender_id, inbound.display_name)
        conv = convs.get_open_conversation(hotel.id, guest, inbound.channel)

        guess = detect_language(text)
        language = resolve_reply_language(guess, conv.language)
        conv.language = language
        guest_msg = convs.add_message(
            conv, MessageRole.GUEST, text, language=guess.detected, external_id=inbound.external_id
        )
        log_event("message_received", conversation_id=conv.id, channel=inbound.channel,
                  language=language, detected=guess.detected, chars=len(text))

        facts = extract_facts(text)
        if facts:
            conv.memory = remember(conv.memory or {}, facts)

        turn = _Turn(session=session, conv=conv, hotel=hotel, inbound=inbound, text=text, language=language)
        turn.intent = self.classifier.classify(text, language)
        guest_msg.intent = turn.intent.intent.value
        log_event("intent_detected", conversation_id=conv.id, intent=turn.intent.intent.value,
                  confidence=turn.intent.confidence, request_type=getattr(turn.intent.request_type, "value", None),
                  source=turn.intent.source)

        if conv.status == ConversationStatus.HANDED_OFF:
            self._while_handed_off(turn)
        elif not self._resolve_pending_offer(turn):
            self._route(turn)

        self._update_failures(turn)
        if turn.reply:
            convs.add_message(conv, MessageRole.BOT, turn.reply, language=language,
                              extra={"sources": turn.sources, "intent": turn.intent.intent.value})
        return AgentReply(
            conversation_id=conv.id,
            text=turn.reply,
            language=language,
            intent=turn.intent.intent.value,
            grounded=turn.grounded,
            sources=turn.sources,
            actions=turn.actions,
            handed_off=conv.status == ConversationStatus.HANDED_OFF,
            knowledge_synthetic=hotel.is_synthetic,
        )

    # -------------------------------------------------------------- routing
    def _route(self, turn: _Turn) -> None:
        result = turn.intent
        assert result is not None
        decision = handoff_for_intent(result)
        if decision is not None:
            # Complaints about a concrete problem also create a work item.
            if result.request_type and result.intent == Intent.COMPLAINT:
                self._create_request(turn, result.request_type)
            if result.intent == Intent.BOOKING_REQUEST:
                self._create_request(turn, RequestType.BOOKING)
            self._handoff(turn, decision)
            return

        if result.intent == Intent.SERVICE_REQUEST:
            self._service_request(turn, result.request_type or RequestType.OTHER)
        elif result.intent == Intent.BOOKING_REQUEST:
            self._create_request(turn, RequestType.BOOKING)
            turn.reply = msg.t("booking_request", turn.language)
            turn.succeeded = True
        elif result.intent == Intent.GENERAL_CONVERSATION:
            turn.reply = self._small_talk(turn)
            turn.succeeded = True
        elif result.intent in (Intent.HOTEL_INFORMATION, Intent.LOCAL_RECOMMENDATION, Intent.UNKNOWN):
            self._answer(turn)
        else:  # defensive: an intent with no route
            self._answer(turn)

    def _answer(self, turn: _Turn) -> None:
        retrieval = self.knowledge.search(turn.text, turn.language)
        log_event("knowledge_retrieved", conversation_id=turn.conv.id, grounded=retrieval.grounded,
                  items=[(i.key, i.score) for i in retrieval.items])
        answer = self.responder.answer(retrieval, turn.language, self._history(turn))
        turn.grounded = answer.grounded
        if answer.grounded:
            turn.reply, turn.sources, turn.succeeded = answer.text, answer.sources, True
            rtype = turn.intent.request_type if turn.intent else None
            if rtype is not None:
                # e.g. "Is late check-in possible?" -> answer, then offer to act.
                turn.reply = f"{turn.reply} {msg.t('offer_request', turn.language)}"
                turn.set_state(pending_offer={"kind": "request", "request_type": rtype.value, "question": turn.text})
            return
        turn.failed = True
        if self._escalate_on_failure(turn):
            return
        if turn.intent and turn.intent.intent == Intent.UNKNOWN:
            turn.reply = msg.t("clarify", turn.language)
        else:
            turn.reply = msg.t("cannot_confirm", turn.language)
            turn.set_state(pending_offer={"kind": "ask_staff", "question": turn.text})

    def _service_request(self, turn: _Turn, request_type: RequestType) -> None:
        """Actions are never confirmed by the bot: relevant policy (if the
        knowledge pack has it) is quoted, then the request goes to staff."""
        retrieval = self.knowledge.search(turn.text, turn.language, k=5)
        policy = ""
        for item in retrieval.grounded_items:
            if request_type.value in item.metadata.get("requires_staff_approval", []):
                policy = item.text_for(turn.language) + " "
                turn.sources = [item.key]
                break
        turn.grounded = bool(policy) or None
        self._create_request(turn, request_type)
        turn.reply = policy + msg.t("request_created", turn.language)
        turn.succeeded = True

    def _small_talk(self, turn: _Turn) -> str:
        signals = " ".join(turn.intent.signals) if turn.intent else ""
        if any(w in signals for w in ("thank", "hvala")):
            return msg.t("thanks", turn.language)
        if any(w in signals for w in ("bye", "dovidjenja", "laku noc")):
            return msg.t("goodbye", turn.language)
        return msg.t("greeting", turn.language, hotel=turn.hotel.name)

    # --------------------------------------------------- dialogue state
    def _resolve_pending_offer(self, turn: _Turn) -> bool:
        offer = turn.state.get("pending_offer")
        if not offer:
            return False
        turn.set_state(pending_offer=None)
        answer = fold(turn.text).strip(" .!")
        if answer in NEGATIVE:
            turn.reply = msg.t("offer_declined", turn.language)
            turn.succeeded = True
            return True
        if answer not in AFFIRMATIVE:
            return False  # guest moved on; handle the message normally
        if offer["kind"] == "request":
            self._create_request(turn, RequestType(offer["request_type"]), guest_message=offer["question"])
            turn.reply = msg.t("request_created", turn.language)
        else:  # ask_staff: forward an unanswerable question to the staff queue
            self._create_request(turn, RequestType.OTHER, guest_message=offer["question"],
                                 summary=f"Guest question the bot could not answer: {offer['question']}")
            turn.reply = msg.t("question_forwarded", turn.language)
        turn.succeeded = True
        return True

    def _while_handed_off(self, turn: _Turn) -> None:
        """A human owns the conversation: store only, unless it is an emergency."""
        if turn.intent and turn.intent.intent == Intent.EMERGENCY:
            decision = handoff_for_intent(turn.intent)
            assert decision is not None
            self._handoff(turn, decision)
        log_event("message_held_for_staff", conversation_id=turn.conv.id)

    # ------------------------------------------------------------ actions
    def _create_request(self, turn: _Turn, request_type: RequestType, *, guest_message: str | None = None,
                        summary: str | None = None) -> None:
        message = guest_message or turn.text
        result = self.tools.call(
            "create_hotel_request",
            ToolContext(session=turn.session, conversation=turn.conv),
            {
                "request_type": request_type.value,
                "summary": summary or f"{request_type.value.replace('_', ' ').capitalize()}: {message}"[:1000],
                "urgency": request_urgency(request_type).value,
                "guest_message": message,
            },
        )
        if result.ok:
            turn.actions.append(ActionTaken(kind="hotel_request", id=result.data["request_id"],
                                            detail={"request_type": request_type.value}))

    def _handoff(self, turn: _Turn, decision: HandoffDecision) -> None:
        handoff = create_handoff(
            turn.session, turn.conv, decision, turn.text,
            turn.intent.intent.value if turn.intent else "UNKNOWN", self.handoff_context,
        )
        turn.actions.append(ActionTaken(kind="handoff", id=handoff.id,
                                        detail={"reason": decision.reason.value, "urgency": handoff.urgency.value}))
        turn.reply = msg.t(decision.reply_key, turn.language)
        turn.set_state(pending_offer=None)
        turn.succeeded = True

    def _escalate_on_failure(self, turn: _Turn) -> bool:
        decision = handoff_for_failures(turn.conv.consecutive_failures + 1, self.max_failures)
        if decision is None:
            return False
        self._handoff(turn, decision)
        return True

    def _update_failures(self, turn: _Turn) -> None:
        if turn.failed and turn.conv.status != ConversationStatus.HANDED_OFF:
            turn.conv.consecutive_failures += 1
        elif turn.succeeded or turn.conv.status == ConversationStatus.HANDED_OFF:
            turn.conv.consecutive_failures = 0

    def _history(self, turn: _Turn) -> list[ChatMessage]:
        recent = ConversationRepository(turn.session).recent_messages(turn.conv, HISTORY_TURNS + 1)[:-1]
        return [
            ChatMessage(role="user" if m.role == MessageRole.GUEST else "assistant", content=m.text)
            for m in recent
            if m.role != MessageRole.SYSTEM
        ]
