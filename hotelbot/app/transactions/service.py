"""External Service Transaction Layer - domain operations.

    request intent -> capability -> provider discovery -> QUOTE (offered)
        -> explicit guest consent to THIS quote -> Action PROPOSED + ExternalTransaction
        -> job: provider_submit (idempotency key) -> SUBMITTED -> ACCEPTED | REJECTED
        -> provider callbacks / staff -> IN_PROGRESS -> COMPLETED | FAILED | CANCELLED

Invariants:
* quote != booking; submitted != accepted; accepted != completed.
* Every status change goes through ActionService (validated + audited).
* Every guest-facing statement about a transaction is derived from stored
  state (app/agent/authority.py) and delivered through the job queue.
* Every provider call that can create an obligation carries an idempotency
  key; retries can never create a second booking.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.actions.executors import ExecutorRegistry
from app.actions.service import ActionService, InvalidTransition
from app.agent.handoff import create_handoff
from app.agent.policies import HandoffDecision, HandoffReason
from app.clock import Clock, as_utc
from app.db.models import (
    Action,
    ActionEvent,
    ActionStatus,
    Conversation,
    ExternalProvider,
    ExternalTransaction,
    MessageRole,
    Property,
    Quote,
    QuoteStatus,
    Stay,
    Urgency,
)
from app.db.repositories import ConversationRepository
from app.jobs.queue import PermanentJobError, RetryableJobError, enqueue
from app.observability import log_event
from app.transactions.format import format_price, format_summary
from app.transactions.providers.base import ProviderError, QuoteRequest, SubmitRequest
from app.transactions.providers.registry import ProviderRegistry
from app.whatsapp.base import MessageTransport

S = ActionStatus
CANCELLABLE = (S.PROPOSED, S.SUBMITTED, S.ACCEPTED)
ACTIVE = (S.PROPOSED, S.SUBMITTED, S.ACCEPTED, S.IN_PROGRESS)
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"   # no I/O: not confusable with digits

_OUTCOME_STATUS = {
    "accepted": S.ACCEPTED, "rejected": S.REJECTED, "in_progress": S.IN_PROGRESS, "completed": S.COMPLETED,
    "failed": S.FAILED, "cancelled": S.CANCELLED,
}


class QuoteNotOpen(Exception):
    pass


class QuoteExpired(Exception):
    pass


@dataclass
class TxnDeps:
    clock: Clock
    providers: ProviderRegistry
    executors: ExecutorRegistry
    transport_for: Callable[[str], MessageTransport]
    handoff_context: int = 10


class TransactionService:
    def __init__(self, session: Session, deps: TxnDeps) -> None:
        self.s = session
        self.deps = deps
        self.clock = deps.clock

    # ------------------------------------------------------------ discovery
    def provider_for(self, property_id: str, declared_provider: str | None, service_type: str) -> ExternalProvider | None:
        """The declared provider if it is active and offers the service,
        otherwise the first active provider of this property offering it."""
        candidates = [p for p in self.s.scalars(
            select(ExternalProvider).where(ExternalProvider.property_id == property_id, ExternalProvider.active)
            .order_by(ExternalProvider.slug))
            if service_type in (p.services or {}).get("service_types", [])]
        if declared_provider:
            candidates = [p for p in candidates if p.slug == declared_provider]
        return candidates[0] if candidates else None

    # ---------------------------------------------------------------- quotes
    def request_quote(self, *, stay: Stay, conv: Conversation, provider: ExternalProvider, service_type: str,
                      details: dict[str, Any], locale: str) -> Quote:
        """Ask the provider for a price. Raises ProviderError on failure."""
        result = self.deps.providers.adapter(provider).request_quote(QuoteRequest(service_type, details, locale))
        self.supersede_open(conv.id)
        quote = Quote(
            code="".join(secrets.choice(_CODE_ALPHABET) for _ in range(4)),
            property_id=stay.property_id, stay_id=stay.id, guest_id=stay.guest_id, conversation_id=conv.id,
            provider_id=provider.id, service_type=service_type, request=details, currency=result.currency,
            amount=result.amount, description=result.description, conditions=result.conditions,
            valid_until=result.valid_until, provider_reference=result.provider_reference, status=QuoteStatus.OFFERED,
            consent={}, created_at=self.clock.now(),
        )
        self.s.add(quote)
        self.s.flush()
        log_event("quote_offered", quote_id=quote.id, provider=provider.slug, service_type=service_type,
                  amount=str(quote.amount), currency=quote.currency)
        return quote

    def open_quote(self, conversation_id: str) -> Quote | None:
        return self.s.scalar(select(Quote).where(Quote.conversation_id == conversation_id,
                                                 Quote.status == QuoteStatus.OFFERED)
                             .order_by(Quote.created_at.desc()).limit(1))

    def supersede_open(self, conversation_id: str) -> None:
        for q in self.s.scalars(select(Quote).where(Quote.conversation_id == conversation_id,
                                                    Quote.status == QuoteStatus.OFFERED)):
            q.status, q.decided_at = QuoteStatus.SUPERSEDED, self.clock.now()

    def is_expired(self, quote: Quote) -> bool:
        return as_utc(quote.valid_until) <= self.clock.now()

    def expire(self, quote: Quote) -> None:
        if quote.status == QuoteStatus.OFFERED:
            quote.status, quote.decided_at = QuoteStatus.EXPIRED, self.clock.now()
            log_event("quote_expired", quote_id=quote.id)

    def decline(self, quote: Quote) -> None:
        quote.status, quote.decided_at = QuoteStatus.DECLINED_BY_GUEST, self.clock.now()
        log_event("quote_declined", quote_id=quote.id)

    def accept(self, quote: Quote, *, message_id: str | None, text: str) -> ExternalTransaction:
        """Record explicit consent and create the transaction. The provider is
        contacted asynchronously by the provider_submit job."""
        if quote.status != QuoteStatus.OFFERED:
            raise QuoteNotOpen(quote.status.value)
        if self.is_expired(quote):
            self.expire(quote)
            raise QuoteExpired(quote.id)
        quote.status, quote.decided_at = QuoteStatus.ACCEPTED_BY_GUEST, self.clock.now()
        quote.consent = {"message_id": message_id, "text": text, "at": self.clock.now().isoformat(),
                         "amount": str(quote.amount), "currency": quote.currency, "code": quote.code}
        action = ActionService(self.s, self.deps.executors).propose(
            property_id=quote.property_id, stay_id=quote.stay_id, conversation_id=quote.conversation_id,
            action_type=quote.service_type, executor="provider",
            summary=f"{quote.service_type} via provider, {format_price(quote.amount, quote.currency)}",
            params={"quote_id": quote.id, "details": quote.request, "amount": str(quote.amount),
                    "currency": quote.currency},
        )
        txn = ExternalTransaction(action_id=action.id, provider_id=quote.provider_id, quote_id=quote.id,
                                  idempotency_key=f"txn-{quote.id}", request=quote.request, submit_attempts=0,
                                  created_at=self.clock.now())
        self.s.add(txn)
        self.s.flush()
        enqueue(self.s, "provider_submit", {"transaction_id": txn.id}, f"submit:{txn.id}", clock=self.clock)
        log_event("quote_accepted", quote_id=quote.id, transaction_id=txn.id, action_id=action.id)
        return txn

    # ---------------------------------------------------------- transactions
    def for_action(self, action_id: str) -> ExternalTransaction | None:
        return self.s.scalar(select(ExternalTransaction).where(ExternalTransaction.action_id == action_id))

    def active_for_stay(self, stay_id: str) -> list[ExternalTransaction]:
        rows = self.s.scalars(select(ExternalTransaction).join(Action, ExternalTransaction.action_id == Action.id)
                              .where(Action.stay_id == stay_id, Action.status.in_(ACTIVE))
                              .order_by(ExternalTransaction.created_at.desc()))
        return list(rows)

    def request_cancel(self, txn: ExternalTransaction) -> bool:
        if txn.action.status not in CANCELLABLE:
            return False
        if txn.action.status == S.PROPOSED:
            # Not yet sent: cancel locally; the pending submit job becomes a no-op.
            self.apply_status(txn, S.CANCELLED, "guest", "cancelled before submission")
        else:
            enqueue(self.s, "provider_cancel", {"transaction_id": txn.id}, f"cancel:{txn.id}", clock=self.clock)
        return True

    def apply_status(self, txn: ExternalTransaction, target: ActionStatus, actor: str,
                     detail: str | None = None) -> str:
        """Move the transaction's action through the state machine; queue the
        guest notification; escalate failures to staff."""
        action = txn.action
        if action.status == target:
            return "already_in_state"
        try:
            ActionService(self.s, self.deps.executors).transition(action, target, actor, detail)
        except InvalidTransition:
            log_event("transaction_invalid_transition", transaction_id=txn.id, current=action.status.value,
                      target=target.value, actor=actor)
            return "invalid_transition"
        self.queue_notification(action, target)
        if target == S.FAILED:
            self._escalate(txn, detail)
        return "applied"

    def queue_notification(self, action: Action, status: ActionStatus, template: str | None = None) -> None:
        key = f"notify:{action.id}:{template or status.value}"
        enqueue(self.s, "notify_guest", {"action_id": action.id, "status": status.value, "template": template},
                key, clock=self.clock)

    def _escalate(self, txn: ExternalTransaction, detail: str | None) -> None:
        conv = self.s.get(Conversation, txn.action.conversation_id) if txn.action.conversation_id else None
        if conv is None:
            return
        create_handoff(self.s, conv, HandoffDecision(HandoffReason.PROVIDER_FAILURE, Urgency.HIGH, "txn_failed"),
                       f"[system] {txn.action.action_type} via provider failed: {detail or 'unknown error'}",
                       "TRANSACTION", self.deps.handoff_context)

    # ---------------------------------------------------------- job handlers
    def run_submit(self, transaction_id: str) -> None:
        txn = self.s.get(ExternalTransaction, transaction_id)
        if txn is None:
            raise PermanentJobError(f"unknown transaction {transaction_id}")
        if txn.action.status != S.PROPOSED:
            return  # already submitted, or cancelled before submission
        quote = txn.quote
        try:
            outcome = self.deps.providers.adapter(txn.provider).submit(SubmitRequest(
                idempotency_key=txn.idempotency_key, service_type=quote.service_type, details=txn.request,
                quote_reference=quote.provider_reference, amount=quote.amount, currency=quote.currency,
                customer_reference=txn.action.stay_id or txn.id,
            ))
        except ProviderError as exc:
            raise (RetryableJobError if exc.retryable else PermanentJobError)(f"{exc.kind}: {exc}") from exc
        actor = f"provider:{txn.provider.slug}"
        txn.provider_reference = outcome.reference or txn.provider_reference
        txn.submitted_at = self.clock.now()
        ActionService(self.s, self.deps.executors).transition(txn.action, S.SUBMITTED, actor)
        txn.action.external_ref = txn.provider_reference
        final = _OUTCOME_STATUS.get(outcome.status)
        if final is None:   # "received": stays SUBMITTED until the provider calls back
            self.queue_notification(txn.action, S.SUBMITTED)
        else:
            self.apply_status(txn, final, actor, outcome.message)
        log_event("transaction_submitted", transaction_id=txn.id, provider=txn.provider.slug,
                  outcome=outcome.status, reference=txn.provider_reference)

    def on_submit_dead(self, transaction_id: str, error: str) -> None:
        txn = self.s.get(ExternalTransaction, transaction_id)
        if txn is None or txn.action.status not in (S.PROPOSED, S.SUBMITTED):
            return
        txn.last_error = error
        txn.action.error = error
        self.apply_status(txn, S.FAILED, "system:retries_exhausted", error)

    def run_cancel(self, transaction_id: str) -> None:
        txn = self.s.get(ExternalTransaction, transaction_id)
        if txn is None or txn.action.status not in CANCELLABLE or not txn.provider_reference:
            return
        try:
            outcome = self.deps.providers.adapter(txn.provider).cancel(txn.provider_reference, f"cancel-{txn.id}")
        except ProviderError as exc:
            raise (RetryableJobError if exc.retryable else PermanentJobError)(f"{exc.kind}: {exc}") from exc
        if outcome.status == "cancelled":
            self.apply_status(txn, S.CANCELLED, f"provider:{txn.provider.slug}", outcome.message)
        else:
            self.queue_notification(txn.action, txn.action.status, template="txn_cancel_refused")
            self._escalate(txn, f"cancellation refused: {outcome.message}")

    def on_cancel_dead(self, transaction_id: str, error: str) -> None:
        txn = self.s.get(ExternalTransaction, transaction_id)
        if txn is not None:
            self.queue_notification(txn.action, txn.action.status, template="txn_cancel_refused")
            self._escalate(txn, f"cancellation could not be delivered: {error}")

    def run_notify(self, action_id: str, status: str, template: str | None) -> None:
        from app.agent.authority import transaction_status_message

        action = self.s.get(Action, action_id)
        if action is None or action.conversation_id is None:
            return
        if template is None and action.status.value != status:
            return  # superseded by a newer state; that state has its own notification
        conv = self.s.get(Conversation, action.conversation_id)
        prop = self.s.get(Property, action.property_id)
        txn = self.for_action(action.id)
        assert conv is not None and prop is not None
        locale = conv.language or prop.default_language
        text = transaction_status_message(self.view(txn, action, prop, locale), locale, template)
        ConversationRepository(self.s).add_message(
            conv, MessageRole.BOT, text, language=conv.language,
            extra={"action_id": action.id, "action_status": action.status.value, "kind": "status_notification"})
        result = self.deps.transport_for(conv.channel).send_text(conv.guest.external_id, text)
        if not result.ok:
            raise RetryableJobError(f"delivery failed: {result.error}")
        log_event("message_sent", channel=conv.channel, kind="status_notification", action_id=action.id,
                  status=action.status.value)

    # ------------------------------------------------------------------ views
    def view(self, txn: ExternalTransaction | None, action: Action, prop: Property, locale: str) -> TxnView:
        was_accepted = self.s.scalar(select(ActionEvent.id).where(
            ActionEvent.action_id == action.id, ActionEvent.to_status == S.ACCEPTED).limit(1)) is not None
        provider_name = txn.provider.name if txn else prop.name
        details = txn.request if txn else {}
        return TxnView(
            status=action.status,
            service_type=action.action_type,
            provider_name=provider_name,
            summary=format_summary(action.action_type, details, locale, prop.timezone, prop.name,
                                   with_label=False) if txn else "",
            reference=(txn.provider_reference if txn else None) or "-",
            was_accepted=was_accepted,
        )


@dataclass(frozen=True)
class TxnView:
    status: ActionStatus
    service_type: str
    provider_name: str
    summary: str
    reference: str
    was_accepted: bool
