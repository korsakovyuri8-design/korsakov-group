"""The conversational core. Every channel (WhatsApp webhook, /api/chat demo,
CLI, evaluation harness) calls `Orchestrator.handle()` - there is exactly one
path from guest message to bot decision.

Per message:
 1. resolve the Property, idempotency check, guest -> Stay -> Conversation
 2. language detection, persist guest message, stay-scoped memory
 3. intent classification
 4. if a human owns the conversation: stay silent (except emergencies)
 5. resolve a pending yes/no offer, if any
 6. route by intent: handoff / action (via capability registry) / action
    status / grounded answer / clarify
 7. failure counting -> escalation per the property's failure policy
 8. persist bot reply; the caller delivers it on its channel

Response authority: every sentence about an action's state comes from
`authority.status_message()` applied to the stored action - never from
model prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.actions.catalog import action_for_topic, action_label
from app.actions.executors import ExecutorRegistry
from app.actions.service import ActionService
from app.agent import messages as msg
from app.agent.authority import status_message
from app.agent.dialogue import is_follow_up, split_clauses
from app.agent.handoff import create_handoff
from app.agent.intents import Intent, IntentClassifier, IntentResult
from app.agent.language import detect_language, resolve_reply_language
from app.agent.memory import extract_facts
from app.agent.policies import (
    AFFIRMATIVE,
    NEGATIVE,
    HandoffDecision,
    handoff_for_failures,
    handoff_for_intent,
)
from app.agent.responder import GroundedAnswer, GroundedResponder
from app.agent.runtime import PropertyDirectory, PropertyRuntime
from app.capabilities.registry import ActionCapability, CapabilityRegistry, CapabilitySpec
from app.db.models import (
    Action,
    ActionStatus,
    Conversation,
    ConversationStatus,
    MessageRole,
    Property,
    RequestType,
    Stay,
)
from app.db.repositories import ActionRepository, ConversationRepository, PropertyRepository
from app.knowledge.schemas import RetrievalResult
from app.llm.base import ChatMessage
from app.observability import log_event
from app.schemas.messages import ActionTaken, AgentReply, InboundMessage
from app.stays.service import StayService
from app.text import fold
from app.tools.registry import ToolContext, ToolRegistry

HISTORY_TURNS = 6


@dataclass
class _Turn:
    """Mutable state for handling one inbound message."""

    session: Session
    runtime: PropertyRuntime
    prop: Property
    stay: Stay
    conv: Conversation
    inbound: InboundMessage
    text: str
    language: str
    actions_service: ActionService
    intent: IntentResult | None = None
    reply: str | None = None
    grounded: bool | None = None
    sources: list[str] = field(default_factory=list)
    actions: list[ActionTaken] = field(default_factory=list)
    failed: bool = False  # bot could not help this turn
    succeeded: bool = False
    facts: dict[str, Any] = field(default_factory=dict)  # guest-stated facts in this message

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
        properties: PropertyDirectory,
        classifier: IntentClassifier,
        responder: GroundedResponder,
        tools: ToolRegistry,
        executors: ExecutorRegistry,
        *,
        handoff_context_messages: int = 10,
        max_inbound_chars: int = 2000,
    ) -> None:
        self.session_factory = session_factory
        self.properties = properties
        self.classifier = classifier
        self.responder = responder
        self.tools = tools
        self.executors = executors
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
        runtime = self.properties.get(inbound.property_slug)
        prop = PropertyRepository(session).get(runtime.property_id)
        if prop is None:
            raise RuntimeError(f"property {runtime.slug!r} not loaded; ingest its pack first")
        convs = ConversationRepository(session)

        if inbound.external_id and convs.message_exists(inbound.external_id):
            log_event("message_duplicate", channel=inbound.channel, external_id=inbound.external_id)
            return AgentReply(conversation_id=None, text=None, language="en", duplicate=True,
                              property_slug=runtime.slug)

        text = inbound.text.strip()[: self.max_chars]
        stays = StayService(session)
        guest = convs.get_or_create_guest(inbound.channel, inbound.sender_id, inbound.display_name)
        stay = stays.current_stay(guest, prop, inbound.channel)
        conv = convs.get_open_conversation(stay, inbound.channel)

        guess = detect_language(text)
        remembered = (guest.preferences or {}).get("language", {}).get("value")
        language = resolve_reply_language(guess, conv.language or remembered)
        conv.language = language
        stays.record_preference(guest, "language", language)
        guest_msg = convs.add_message(
            conv, MessageRole.GUEST, text, language=guess.locale, external_id=inbound.external_id
        )
        log_event("message_received", conversation_id=conv.id, property=runtime.slug, stay_id=stay.id,
                  channel=inbound.channel, language=language, detected=guess.detected, chars=len(text))

        facts = extract_facts(text)
        stays.record_facts(stay, facts)

        turn = _Turn(session=session, runtime=runtime, prop=prop, stay=stay, conv=conv, inbound=inbound,
                     text=text, language=language, actions_service=ActionService(session, self.executors),
                     facts=facts)
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
            knowledge_synthetic=prop.is_synthetic,
            property_slug=runtime.slug,
            stay_id=stay.id,
        )

    # -------------------------------------------------------------- routing
    def _route(self, turn: _Turn) -> None:
        result = turn.intent
        assert result is not None
        decision = handoff_for_intent(result)
        if decision is not None:
            # A complaint about a concrete problem also creates a work item;
            # a booking change is recorded as a booking inquiry for staff.
            if result.intent == Intent.COMPLAINT and result.request_type:
                self._submit(turn, action_for_topic(result.request_type))
            if result.intent == Intent.BOOKING_REQUEST:
                self._submit(turn, "booking_inquiry")
            self._handoff(turn, decision)
            return

        if result.intent == Intent.SERVICE_REQUEST:
            self._perform_action(turn, action_for_topic(result.request_type or RequestType.OTHER))
        elif result.intent == Intent.BOOKING_REQUEST:
            self._perform_action(turn, "booking_inquiry")
        elif result.intent == Intent.REQUEST_STATUS:
            self._report_status(turn)
        elif turn.facts and result.intent in (Intent.UNKNOWN, Intent.GENERAL_CONVERSATION):
            # "We are 2 adults arriving 20 December": acknowledge what was noted,
            # explicitly without confirming anything.
            turn.reply = msg.t("facts_noted", turn.language, facts=msg.describe_facts(turn.facts, turn.language))
            turn.succeeded = True
        elif result.intent == Intent.GENERAL_CONVERSATION:
            turn.reply = self._small_talk(turn)
            turn.succeeded = True
        else:  # HOTEL_INFORMATION, LOCAL_RECOMMENDATION, UNKNOWN
            self._answer(turn)

    # ------------------------------------------------------------- actions
    def _perform_action(self, turn: _Turn, action_type: str, *, guest_message: str | None = None,
                        summary: str | None = None) -> None:
        """Do what the property's capabilities allow - and nothing more."""
        capabilities = turn.runtime.capabilities
        if not capabilities.can(action_type):
            log_event("capability_unavailable", property=turn.runtime.slug, action_type=action_type)
            # The property's own information may still help ("fresh towels are in
            # the wardrobe") - quote it before stating the limitation.
            info = self._knowledge_hint(turn)
            turn.reply = info + msg.t("capability_unavailable", turn.language,
                                      action=action_label(action_type, turn.language))
            if capabilities.can("staff_question"):
                turn.set_state(pending_offer={"kind": "ask_staff", "question": guest_message or turn.text})
            turn.succeeded = True
            return

        policy = "" if guest_message else self._policy_quote(turn, action_type)
        action = self._submit(turn, action_type, guest_message=guest_message, summary=summary)
        if action is None:
            turn.reply = msg.t("cannot_confirm", turn.language)
            turn.failed = True
            return
        if action.status == ActionStatus.FAILED:
            turn.reply = policy + self._after_failure(turn, action, guest_message, summary)
        elif action.status == ActionStatus.SUBMITTED and action_type == "booking_inquiry":
            turn.reply = policy + msg.t("booking_request", turn.language)
        else:
            turn.reply = policy + status_message(action, turn.language, turn.prop.name)
        turn.succeeded = True

    def _after_failure(self, turn: _Turn, failed: Action, guest_message: str | None, summary: str | None) -> str:
        """An integration failed: say so, and fall back to staff if possible.
        Never imply the failed submission went through."""
        label = action_label(failed.action_type, turn.language)
        if failed.executor != "staff" and turn.runtime.capabilities.has_integration("human_staff"):
            fallback = self._submit(turn, failed.action_type, guest_message=guest_message, summary=summary,
                                    executor_override="staff")
            if fallback is not None and fallback.status == ActionStatus.SUBMITTED:
                return msg.t("action_failed_fallback", turn.language, action=label)
        return msg.t("action_failed", turn.language, action=label)

    def _submit(self, turn: _Turn, action_type: str, *, guest_message: str | None = None,
                summary: str | None = None, executor_override: str | None = None) -> Action | None:
        message = guest_message or turn.text
        capabilities = turn.runtime.capabilities
        if executor_override:
            # Re-route through a different executor (e.g. staff fallback).
            capabilities = CapabilityRegistry(CapabilitySpec(
                actions={action_type: ActionCapability(executor=executor_override)}, integrations=["human_staff"]))
        if not capabilities.can(action_type):
            return None
        result = self.tools.call(
            "submit_action",
            ToolContext(session=turn.session, conversation=turn.conv, stay=turn.stay,
                        capabilities=capabilities, actions=turn.actions_service),
            {
                "action_type": action_type,
                "summary": summary or f"{action_type.replace('_', ' ').capitalize()}: {message}"[:1000],
                "guest_message": message,
            },
        )
        if not result.ok:
            return None
        action = ActionRepository(turn.session).get(result.data["action_id"])
        assert action is not None
        turn.actions.append(ActionTaken(
            kind="hotel_request",  # Core v1 API name; detail.action_type is the generic type
            id=action.id,
            detail={"request_type": action.request_type.value, "action_type": action.action_type,
                    "status": action.status.value, "executor": action.executor},
        ))
        return action

    def _knowledge_hint(self, turn: _Turn) -> str:
        retrieval = turn.runtime.knowledge.search(turn.text, turn.language, k=1)
        if not retrieval.grounded:
            return ""
        item = retrieval.items[0]
        turn.sources, turn.grounded = [item.key], True
        return item.text_for(turn.language) + " "

    def _policy_quote(self, turn: _Turn, action_type: str) -> str:
        """Quote the property's own policy for this kind of request, if the
        pack has one (e.g. "Arrivals after 22:00 must be arranged...")."""
        topic = turn.intent.request_type.value if turn.intent and turn.intent.request_type else None
        retrieval = turn.runtime.knowledge.search(turn.text, turn.language, k=5)
        for item in retrieval.grounded_items:
            approvals = item.metadata.get("requires_staff_approval", [])
            if action_type in approvals or (topic and topic in approvals):
                turn.sources = [item.key]
                turn.grounded = True
                return item.text_for(turn.language) + " "
        return ""

    def _report_status(self, turn: _Turn) -> None:
        """Answer "is my request confirmed?" strictly from stored action state."""
        actions = [a for a in ActionRepository(turn.session).list(stay_id=turn.stay.id)
                   if a.status != ActionStatus.PROPOSED]
        wanted = turn.intent.request_type if turn.intent else None
        if wanted is not None:
            actions = [a for a in actions if a.request_type == wanted] or actions
        if not actions:
            turn.reply = msg.t("no_actions_yet", turn.language)
        else:
            turn.reply = status_message(actions[0], turn.language, turn.prop.name)
        turn.succeeded = True

    # ----------------------------------------------------------- knowledge
    def _answer(self, turn: _Turn) -> None:
        last_question = turn.state.get("last_question")
        query = turn.text
        if last_question and is_follow_up(turn.text, turn.language):
            query = f"{last_question} {turn.text}"
        turn.set_state(last_question=query)

        clauses = split_clauses(query, turn.language)
        texts: list[str] = []
        unknown_parts = 0
        prev_clause = ""
        for clause in clauses:
            clause_query = f"{prev_clause} {clause}" if prev_clause and is_follow_up(clause, turn.language) else clause
            prev_clause = clause
            retrieval = turn.runtime.knowledge.search(clause_query, turn.language)
            log_event("knowledge_retrieved", conversation_id=turn.conv.id, grounded=retrieval.grounded,
                      items=[(i.key, i.score) for i in retrieval.items])
            if self._conflicting(retrieval, turn.language):
                log_event("knowledge_conflict", conversation_id=turn.conv.id,
                          items=[i.key for i in retrieval.grounded_items])
                turn.reply, turn.grounded = msg.t("knowledge_conflict", turn.language), False
                if turn.runtime.capabilities.can("staff_question"):
                    turn.set_state(pending_offer={"kind": "ask_staff", "question": turn.text})
                turn.succeeded = True
                return
            answer = self._grounded_answer(turn, retrieval)
            if answer.grounded and answer.text:
                new_sources = [s for s in answer.sources if s not in turn.sources]
                if new_sources:
                    texts.append(answer.text)
                    turn.sources.extend(new_sources)
            else:
                unknown_parts += 1

        if texts:
            turn.grounded, turn.succeeded = True, True
            if unknown_parts:
                texts.append(msg.t("partial_unknown", turn.language))
            turn.reply = " ".join(texts)
            rtype = turn.intent.request_type if turn.intent else None
            if rtype is not None and not unknown_parts:
                # e.g. "Is late check-in possible?" -> answer, then offer to act.
                action_type = action_for_topic(rtype)
                if turn.runtime.capabilities.can(action_type):
                    turn.reply = f"{turn.reply} {msg.t('offer_request', turn.language)}"
                    turn.set_state(pending_offer={"kind": "request", "action_type": action_type,
                                                  "question": turn.text})
            return

        turn.grounded, turn.failed = False, True
        if self._escalate_on_failure(turn):
            return
        if turn.intent and turn.intent.intent == Intent.UNKNOWN:
            turn.reply = msg.t("clarify", turn.language)
        elif turn.runtime.capabilities.can("staff_question"):
            turn.reply = msg.t("cannot_confirm", turn.language)
            turn.set_state(pending_offer={"kind": "ask_staff", "question": turn.text})
        else:
            turn.reply = msg.t("partial_unknown", turn.language)

    def _grounded_answer(self, turn: _Turn, retrieval: RetrievalResult) -> GroundedAnswer:
        statuses = [a.status for a in ActionRepository(turn.session).list(stay_id=turn.stay.id)]
        return self.responder.answer(
            retrieval, turn.language, self._history(turn),
            property_name=turn.prop.name, property_type=turn.prop.property_type.value,
            action_statuses=statuses,
        )

    @staticmethod
    def _conflicting(retrieval: RetrievalResult, language: str) -> bool:
        """Two top-scoring items about the same topic that say different things."""
        items = retrieval.grounded_items
        if len(items) < 2:
            return False
        top = items[0].score
        contenders = [i for i in items if i.score >= top * 0.9]
        seen: dict[str, str] = {}
        for item in contenders:
            text = item.text_for(language)
            if item.topic in seen and seen[item.topic] != text:
                return True
            seen[item.topic] = text
        return False

    def _small_talk(self, turn: _Turn) -> str:
        signals = " ".join(turn.intent.signals) if turn.intent else ""
        if any(w in signals for w in ("thank", "hvala", "spasibo")):
            return msg.t("thanks", turn.language)
        if any(w in signals for w in ("bye", "dovidjenja", "laku noc", "poka", "do svidaniya")):
            return msg.t("goodbye", turn.language)
        return msg.t("greeting", turn.language, property=turn.prop.name)

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
            action_type = offer.get("action_type") or action_for_topic(RequestType(offer["request_type"]))
            self._perform_action(turn, action_type, guest_message=offer["question"])
        else:  # ask_staff: forward an unanswerable question to the staff queue
            action = self._submit(turn, "staff_question", guest_message=offer["question"],
                                  summary=f"Guest question the bot could not answer: {offer['question']}")
            turn.reply = msg.t("question_forwarded" if action is not None else "cannot_confirm", turn.language)
            turn.succeeded = True
        return True

    def _while_handed_off(self, turn: _Turn) -> None:
        """A human owns the conversation: store only, unless it is an emergency."""
        if turn.intent and turn.intent.intent == Intent.EMERGENCY:
            decision = handoff_for_intent(turn.intent)
            assert decision is not None
            self._handoff(turn, decision)
        log_event("message_held_for_staff", conversation_id=turn.conv.id)

    # ------------------------------------------------------------ handoff
    def _handoff(self, turn: _Turn, decision: HandoffDecision) -> None:
        handoff = create_handoff(
            turn.session, turn.conv, decision, turn.text,
            turn.intent.intent.value if turn.intent else "UNKNOWN", self.handoff_context,
        )
        turn.actions.append(ActionTaken(kind="handoff", id=handoff.id,
                                        detail={"reason": decision.reason.value, "urgency": handoff.urgency.value}))
        if decision.reply_key == "emergency":
            number = turn.runtime.emergency_number
            turn.reply = (msg.t("emergency", turn.language, emergency_number=number) if number
                          else msg.t("emergency_no_number", turn.language))
        else:
            turn.reply = msg.t(decision.reply_key, turn.language)
        turn.set_state(pending_offer=None)
        turn.succeeded = True

    def _escalate_on_failure(self, turn: _Turn) -> bool:
        intent = turn.intent.intent if turn.intent else None
        decision = handoff_for_failures(turn.conv.consecutive_failures + 1, turn.runtime.failure_policy, intent)
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
